"""Integration tests for mirroring a household's meal plan to a Google calendar.

Google is a fake here (`tests.utils.fake_google_calendar`): an in-memory calendar behind an
`httpx.MockTransport`, put in place of the one function that builds HTTP clients for Google and
the one that loads the server's credential. Unless a test asks for the `google` fixture there are
no credentials at all and any attempt to build a client fails the test, so nothing in this file
can reach the real thing.

The plan is changed through the real meal-plan endpoints. `TestClient` runs a request's background
tasks before it returns, so by the time a `POST /api/households/mealplans` comes back the sync it
set off has already run against the fake.

The properties that carry the feature, each with a test: the calendar's events equal the plan after
every change; a second sync changes nothing; a duplicate is removed; an event without this
household's mark is never touched; one household's mirror is invisible to another's; a Google
failure neither fails the plan change nor goes unrecorded, and heals; and a household that is not
set up costs Google nothing.

`unique_user` and `h2_user` are module-scoped, so the autouse fixture empties both plans and turns
both mirrors off on the way in and on the way out.
"""

from dataclasses import replace
from datetime import date, datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

from mealie.core.config import get_app_settings
from mealie.schema.household.calendar_sync import CalendarSyncUpdate
from mealie.services.google_calendar import client as google_client
from mealie.services.google_calendar import credentials as google_credentials
from mealie.services.household_services.calendar_sync import CalendarSyncService, household_repos, window_start
from mealie.services.scheduler.tasks.sync_google_calendars import sync_google_calendars
from tests.utils import api_routes
from tests.utils.assertion_helpers import assert_deserialize
from tests.utils.factories import random_string
from tests.utils.fake_google_calendar import (
    SERVICE_ACCOUNT_EMAIL,
    FakeGoogleCalendar,
    private_key_pem,
    service_account_credentials,
)
from tests.utils.fixture_schemas import TestUser

CALENDAR = "dinner-plan@group.calendar.google.com"
OTHER_CALENDAR = "someone-elses@group.calendar.google.com"

HOUSEHOLD_PROPERTY = "mealieHousehold"
ENTRY_PROPERTY = "mealieEntry"
DATE_PROPERTY = "mealieDate"

# the zone the server is told it is in: `clean_slate` sets `TZ` to it for every test
ZONE = ZoneInfo("America/Denver")

# a day comfortably inside the mirror's window, whatever the server's clock
DAY = window_start(datetime.now(ZONE).date()) + timedelta(days=3)


def day(offset: int = 0) -> str:
    return (DAY + timedelta(days=offset)).isoformat()


def dinner_time(offset: int = 0) -> tuple[dict, dict]:
    """The start and end a dinner's event should have: six to eight that evening, server time."""

    def edge(hour: int) -> dict:
        at = datetime.combine(DAY + timedelta(days=offset), time(hour), tzinfo=ZONE)
        return {"dateTime": at.isoformat(), "timeZone": "America/Denver"}

    return edge(18), edge(20)


def status(api_client: TestClient, user: TestUser) -> dict:
    return assert_deserialize(api_client.get(api_routes.households_calendar_sync, headers=user.token), 200)


def save(api_client: TestClient, user: TestUser, expected_status: int = 200, **payload) -> dict:
    response = api_client.put(api_routes.households_calendar_sync, json=payload, headers=user.token)
    assert response.status_code == expected_status, response.text
    return response.json()


def sync_now(api_client: TestClient, user: TestUser, expected_status: int = 200) -> dict:
    response = api_client.post(api_routes.households_calendar_sync_sync, headers=user.token)
    assert response.status_code == expected_status, response.text
    return response.json()


def configure_as_server(user: TestUser, calendar_id: str | None, enabled: bool) -> None:
    """Set a household's mirror the way the API would, without the permission the API asks for.

    `h2_user` cannot manage its household, which is what the permission test wants and what the
    isolation tests have to get around. Uses a session of its own, like everything the server does
    outside a request.
    """

    with household_repos(user._group_id, user._household_id) as repos:
        CalendarSyncService(repos).configure(CalendarSyncUpdate(calendar_id=calendar_id, enabled=enabled))


def make_recipe(api_client: TestClient, user: TestUser) -> dict:
    response = api_client.post(api_routes.recipes, json={"name": random_string()}, headers=user.token)
    assert response.status_code == 201, response.text
    return assert_deserialize(api_client.get(api_routes.recipes_slug(response.json()), headers=user.token), 200)


def plan(api_client: TestClient, user: TestUser, on: str, **fields) -> dict:
    """Add an entry to the plan through the real endpoint. A plan change must always succeed."""

    payload = {"date": on, "entryType": "dinner", "title": "", "text": "", **fields}
    if not payload.get("recipeId") and not payload["title"]:
        payload["title"] = random_string()

    response = api_client.post(api_routes.households_mealplans, json=payload, headers=user.token)
    assert response.status_code == 201, response.text
    return response.json()


def unplan(api_client: TestClient, user: TestUser, entry_id: int) -> None:
    response = api_client.delete(api_routes.households_mealplans_item_id(entry_id), headers=user.token)
    assert response.status_code == 200, response.text


def empty_plan(api_client: TestClient, user: TestUser) -> None:
    response = api_client.get(api_routes.households_mealplans, params={"perPage": -1}, headers=user.token)
    for entry in assert_deserialize(response, 200)["items"]:
        unplan(api_client, user, entry["id"])


def owned_event(household_id: str, entry_id: int | str, on: str, summary: str = "Dinner: something") -> dict:
    """An event as the mirror would have written it, for seeding the calendar by hand."""

    return {
        "summary": summary,
        "description": "",
        "start": {"date": on},
        "end": {"date": (date.fromisoformat(on) + timedelta(days=1)).isoformat()},
        "transparency": "transparent",
        "extendedProperties": {
            "private": {HOUSEHOLD_PROPERTY: household_id, ENTRY_PROPERTY: str(entry_id), DATE_PROPERTY: on}
        },
    }


def _no_network() -> httpx.Client:
    raise AssertionError("this test tried to build a client for the real Google")


@pytest.fixture(autouse=True)
def clean_slate(api_client: TestClient, unique_user: TestUser, h2_user: TestUser, monkeypatch: pytest.MonkeyPatch):
    # default-deny: no credentials and no way out unless a test takes the `google` fixture
    monkeypatch.setattr(google_credentials, "load_credentials", lambda: None)
    monkeypatch.setattr(google_client, "build_http_client", _no_network)
    monkeypatch.setenv("TZ", ZONE.key)
    google_credentials.token_cache.clear()

    def reset() -> None:
        for user in (unique_user, h2_user):
            # the mirror goes off first, so emptying the plan sets off no syncs
            configure_as_server(user, None, False)
            empty_plan(api_client, user)

    reset()
    yield
    reset()
    google_credentials.token_cache.clear()


@pytest.fixture
def google(monkeypatch: pytest.MonkeyPatch) -> FakeGoogleCalendar:
    """The server has a service-account credential, and Google is in memory."""

    fake = FakeGoogleCalendar()
    credentials = service_account_credentials()
    monkeypatch.setattr(google_credentials, "load_credentials", lambda: credentials)
    monkeypatch.setattr(google_client, "build_http_client", fake.client)
    return fake


@pytest.fixture
def mirrored(api_client: TestClient, unique_user: TestUser, google: FakeGoogleCalendar) -> FakeGoogleCalendar:
    """`unique_user`'s household mirrors to CALENDAR, and the fake's logs start empty from here."""

    google.add_calendar(CALENDAR)
    save(api_client, unique_user, calendarId=CALENDAR, enabled=True)
    google.calls.clear()
    return google


# ---------------------------------------------------------------------------
# status and settings


def test_status_before_anything_is_set_up(api_client: TestClient, unique_user: TestUser):
    assert status(api_client, unique_user) == {
        "configured": False,
        "accountEmail": None,
        "calendarId": None,
        "enabled": False,
        "lastSyncedAt": None,
        "lastError": None,
    }


def test_status_names_the_account_to_share_with_and_nothing_of_the_credential(
    api_client: TestClient, unique_user: TestUser, google: FakeGoogleCalendar
):
    response = api_client.get(api_routes.households_calendar_sync, headers=unique_user.token)

    assert response.json()["configured"] is True
    assert response.json()["accountEmail"] == SERVICE_ACCOUNT_EMAIL
    assert set(response.json()) == {"configured", "accountEmail", "calendarId", "enabled", "lastSyncedAt", "lastError"}
    for line in private_key_pem().splitlines():
        assert line not in response.text
    # reading the status asks Google nothing
    assert google.request_count == 0


def test_changing_the_settings_needs_permission_to_manage_the_household(
    api_client: TestClient, h2_user: TestUser, google: FakeGoogleCalendar
):
    google.add_calendar(CALENDAR)

    save(api_client, h2_user, expected_status=403, calendarId=CALENDAR, enabled=True)

    # refused before anything was asked of Google or written down, but any member may still look
    assert google.request_count == 0
    assert status(api_client, h2_user)["calendarId"] is None
    assert status(api_client, h2_user)["enabled"] is False


def test_enabling_probes_the_calendar_and_then_saves(
    api_client: TestClient, unique_user: TestUser, google: FakeGoogleCalendar
):
    google.add_calendar(CALENDAR)

    saved = save(api_client, unique_user, calendarId=f"  {CALENDAR}  ", enabled=True)

    assert saved["enabled"] is True
    assert saved["calendarId"] == CALENDAR
    assert status(api_client, unique_user)["enabled"] is True
    # one cheap read, the same call a sync opens with, and nothing written by the probe
    assert google.calls == [("GET", f"{CALENDAR}/events", {"maxResults": "1"})]
    assert google.events(CALENDAR) == []


def test_enabling_a_calendar_nobody_shared_is_refused_and_names_the_account(
    api_client: TestClient, unique_user: TestUser, google: FakeGoogleCalendar
):
    refused = api_client.put(
        api_routes.households_calendar_sync,
        json={"calendarId": CALENDAR, "enabled": True},
        headers=unique_user.token,
    )

    assert refused.status_code == 400, refused.text
    message = refused.json()["detail"]["message"]
    assert SERVICE_ACCOUNT_EMAIL in message
    assert "Make changes to events" in message
    assert "404" in message
    for line in private_key_pem().splitlines():
        assert line not in refused.text

    # nothing was saved: the household is not left pointing at a calendar it cannot reach
    assert status(api_client, unique_user)["enabled"] is False
    assert status(api_client, unique_user)["calendarId"] is None


def test_a_refusal_names_even_a_very_long_account_address_in_full(
    api_client: TestClient, unique_user: TestUser, google: FakeGoogleCalendar, monkeypatch: pytest.MonkeyPatch
):
    # a service account's address is as long as its name and its project's id put together
    long_address = "meal-plan-calendar-mirror-kitchen@the-household-automation-project.iam.gserviceaccount.com"
    credentials = replace(service_account_credentials(), client_email=long_address)
    monkeypatch.setattr(google_credentials, "load_credentials", lambda: credentials)

    refused = save(api_client, unique_user, expected_status=400, calendarId=CALENDAR, enabled=True)

    message = refused["detail"]["message"]
    assert long_address in message
    assert message.endswith("(Google Calendar answered 404: Not Found)")
    assert status(api_client, unique_user)["accountEmail"] == long_address


def test_enabling_a_calendar_shared_read_only_is_refused(
    api_client: TestClient, unique_user: TestUser, google: FakeGoogleCalendar
):
    google.add_calendar(CALENDAR, role="reader")

    refused = save(api_client, unique_user, expected_status=400, calendarId=CALENDAR, enabled=True)

    assert SERVICE_ACCOUNT_EMAIL in refused["detail"]["message"]
    assert "cannot change it" in refused["detail"]["message"]
    assert "Make changes to events" in refused["detail"]["message"]
    assert status(api_client, unique_user)["enabled"] is False
    assert google.writes() == []


def test_enabling_when_google_refuses_the_servers_credential_says_so(
    api_client: TestClient, unique_user: TestUser, google: FakeGoogleCalendar
):
    google.add_calendar(CALENDAR)
    google.fail_sign_in(400, "invalid_grant", "Invalid JWT Signature.")

    refused = save(api_client, unique_user, expected_status=400, calendarId=CALENDAR, enabled=True)

    assert "invalid_grant" in refused["detail"]["message"]
    assert SERVICE_ACCOUNT_EMAIL in refused["detail"]["message"]
    assert status(api_client, unique_user)["enabled"] is False


def test_enabling_without_server_credentials_is_refused_but_the_calendar_can_be_noted(
    api_client: TestClient, unique_user: TestUser
):
    refused = save(api_client, unique_user, expected_status=400, calendarId=CALENDAR, enabled=True)
    assert "GOOGLE_CALENDAR_CREDENTIALS" in refused["detail"]["message"]
    assert status(api_client, unique_user)["calendarId"] is None

    # saving it switched off talks to nobody, so it works on a server with no credentials
    saved = save(api_client, unique_user, calendarId=CALENDAR, enabled=False)
    assert saved["calendarId"] == CALENDAR
    assert saved["enabled"] is False
    assert saved["configured"] is False


def test_enabling_needs_a_calendar_id(api_client: TestClient, unique_user: TestUser, google: FakeGoogleCalendar):
    for blank in ({}, {"calendarId": None}, {"calendarId": "   "}):
        refused = save(api_client, unique_user, expected_status=400, enabled=True, **blank)
        assert "Calendar ID" in refused["detail"]["message"]

    assert google.request_count == 0


def test_switching_off_asks_google_nothing_and_keeps_the_calendar_id(
    api_client: TestClient, unique_user: TestUser, mirrored: FakeGoogleCalendar
):
    saved = save(api_client, unique_user, calendarId=CALENDAR, enabled=False)

    assert saved["enabled"] is False
    assert saved["calendarId"] == CALENDAR
    assert mirrored.calls == []


def test_pointing_at_another_calendar_forgets_how_the_old_one_went(
    api_client: TestClient, unique_user: TestUser, mirrored: FakeGoogleCalendar
):
    plan(api_client, unique_user, day())
    assert status(api_client, unique_user)["lastSyncedAt"]

    mirrored.add_calendar(OTHER_CALENDAR)
    moved = save(api_client, unique_user, calendarId=OTHER_CALENDAR, enabled=True)

    assert moved["calendarId"] == OTHER_CALENDAR
    assert moved["lastSyncedAt"] is None
    assert moved["lastError"] is None


# ---------------------------------------------------------------------------
# the plan, mirrored


def test_creating_updating_and_deleting_an_entry_is_mirrored(
    api_client: TestClient, unique_user: TestUser, mirrored: FakeGoogleCalendar
):
    recipe = make_recipe(api_client, unique_user)
    group_slug = assert_deserialize(api_client.get(api_routes.groups_self, headers=unique_user.token), 200)["slug"]
    link = f"{get_app_settings().BASE_URL}/g/{group_slug}/r/{recipe['slug']}"

    # created
    entry = plan(api_client, unique_user, day(), recipeId=recipe["id"], text="Double it")

    [event] = mirrored.events(CALENDAR)
    assert event["summary"] == f"Dinner: {recipe['name']}"
    assert event["description"] == f"Double it\n\n{link}"
    assert (event["start"], event["end"]) == dinner_time(), "a dinner is on the calendar from six to eight"
    assert event["transparency"] == "transparent"
    assert event["source"] == {"title": recipe["name"], "url": link}
    assert event["extendedProperties"] == {
        "private": {
            HOUSEHOLD_PROPERTY: unique_user.household_id,
            ENTRY_PROPERTY: str(entry["id"]),
            DATE_PROPERTY: day(),
        }
    }

    after_create = status(api_client, unique_user)
    assert after_create["lastSyncedAt"]
    assert after_create["lastError"] is None

    # updated: moved a day, to lunch, and from a recipe to a note
    response = api_client.put(
        api_routes.households_mealplans_item_id(entry["id"]),
        json={**entry, "date": day(1), "entryType": "lunch", "recipeId": None, "title": "Leftovers", "text": ""},
        headers=unique_user.token,
    )
    assert response.status_code == 200, response.text

    [moved] = mirrored.events(CALENDAR)
    assert moved["id"] == event["id"], "an update patches the entry's event; it does not replace it"
    assert moved["summary"] == "Lunch: Leftovers"
    assert moved["description"] == ""
    assert moved["start"] == {"date": day(1)}
    assert moved["end"] == {"date": day(2)}
    assert moved["extendedProperties"]["private"][DATE_PROPERTY] == day(1)
    assert "source" not in moved

    # deleted
    unplan(api_client, unique_user, entry["id"])
    assert mirrored.events(CALENDAR) == []

    assert [method for method, _ in mirrored.writes()] == ["POST", "PATCH", "DELETE"]
    # three syncs, and one sign-in between them (the probe's token is still good)
    assert len(mirrored.token_requests) == 1


def test_a_whole_week_replaced_in_a_burst_ends_up_equal_to_the_plan(
    api_client: TestClient, unique_user: TestUser, mirrored: FakeGoogleCalendar
):
    old_week = [plan(api_client, unique_user, day(offset), title=f"old {offset}") for offset in range(7)]

    # the planner's shape: the seven new dinners, then the seven old ones gone
    for offset in range(7):
        plan(api_client, unique_user, day(offset), title=f"new {offset}")
    for entry in old_week:
        unplan(api_client, unique_user, entry["id"])

    events = mirrored.events(CALENDAR)
    assert sorted(event["summary"] for event in events) == sorted(f"Dinner: new {offset}" for offset in range(7))
    assert sorted(event["start"]["dateTime"] for event in events) == [
        dinner_time(offset)[0]["dateTime"] for offset in range(7)
    ]


def test_sync_now_reports_what_changed_and_a_second_sync_changes_nothing(
    api_client: TestClient, unique_user: TestUser, google: FakeGoogleCalendar
):
    # planned before the mirror was on, so nothing has reached the calendar yet
    entries = [plan(api_client, unique_user, day(offset)) for offset in range(5)]
    google.add_calendar(CALENDAR)
    google.page_size = 2  # five events come back over three pages; a reconcile must read them all
    save(api_client, unique_user, calendarId=CALENDAR, enabled=True)
    assert google.events(CALENDAR) == []

    assert sync_now(api_client, unique_user) == {"created": 5, "updated": 0, "deleted": 0}

    first = google.events(CALENDAR)
    assert sorted(e["extendedProperties"]["private"][ENTRY_PROPERTY] for e in first) == sorted(
        str(entry["id"]) for entry in entries
    )
    writes = google.writes()
    assert len(writes) == 5

    assert sync_now(api_client, unique_user) == {"created": 0, "updated": 0, "deleted": 0}

    assert google.events(CALENDAR) == first
    assert google.writes() == writes, "an idempotent sync writes nothing"
    assert [query.get("pageToken") for query in google.lists()[-3:]] == [None, "2", "4"]


def test_a_duplicate_event_for_one_entry_is_removed(
    api_client: TestClient, unique_user: TestUser, mirrored: FakeGoogleCalendar
):
    entry = plan(api_client, unique_user, day(), title="Lentil Tacos")
    [original] = mirrored.events(CALENDAR)

    # what two processes syncing at once would leave behind: the same entry, written twice
    duplicate = mirrored.seed_event(CALENDAR, owned_event(unique_user.household_id, entry["id"], day()))
    assert len(mirrored.events(CALENDAR)) == 2

    assert sync_now(api_client, unique_user) == {"created": 0, "updated": 0, "deleted": 1}

    [kept] = mirrored.events(CALENDAR)
    assert kept["id"] == original["id"], "the older event is the one kept"
    assert kept["id"] != duplicate["id"]
    assert kept["summary"] == "Dinner: Lentil Tacos"


def test_a_dinner_written_as_an_all_day_event_becomes_six_to_eight_without_a_second_event(
    api_client: TestClient, unique_user: TestUser, mirrored: FakeGoogleCalendar
):
    entry = plan(api_client, unique_user, day(), title="Lentil Tacos")
    lunch = plan(api_client, unique_user, day(), title="Soup", entryType="lunch")
    by_entry = {e["extendedProperties"]["private"][ENTRY_PROPERTY]: e for e in mirrored.events(CALENDAR)}
    written = by_entry[str(entry["id"])]

    # what a sync from before dinners had an hour left on the calendar
    mirrored.calendars[CALENDAR][written["id"]] |= {"start": {"date": day()}, "end": {"date": day(1)}}

    assert sync_now(api_client, unique_user) == {"created": 0, "updated": 1, "deleted": 0}

    after = {e["extendedProperties"]["private"][ENTRY_PROPERTY]: e for e in mirrored.events(CALENDAR)}
    assert len(mirrored.events(CALENDAR)) == 2, "converted in place: no second event for the dinner"
    converted = after[str(entry["id"])]
    assert converted["id"] == written["id"]
    assert (converted["start"], converted["end"]) == dinner_time()
    assert converted["transparency"] == "transparent"
    assert converted["extendedProperties"] == written["extendedProperties"]

    # the lunch is as it always was: all day
    assert after[str(lunch["id"])] == by_entry[str(lunch["id"])]
    assert after[str(lunch["id"])]["start"] == {"date": day()}
    assert after[str(lunch["id"])]["end"] == {"date": day(1)}

    assert sync_now(api_client, unique_user) == {"created": 0, "updated": 0, "deleted": 0}


def test_an_event_without_this_households_mark_is_never_touched(
    api_client: TestClient, unique_user: TestUser, mirrored: FakeGoogleCalendar
):
    entry = plan(api_client, unique_user, day(), title="Lentil Tacos")

    # a person's own event on the same day, with a name that looks like one of ours
    dentist = mirrored.seed_event(
        CALENDAR, {"summary": "Dinner: with the dentist", "start": {"date": day()}, "end": {"date": day(1)}}
    )
    # another household's mirror in the same calendar, even claiming the same entry id
    neighbours = mirrored.seed_event(CALENDAR, owned_event(str(uuid4()), entry["id"], day()))
    # the mark in the wrong place does not make an event ours
    shared_mark = mirrored.seed_event(
        CALENDAR,
        {
            "summary": "Potluck",
            "start": {"date": day()},
            "end": {"date": day(1)},
            "extendedProperties": {"shared": {HOUSEHOLD_PROPERTY: unique_user.household_id, ENTRY_PROPERTY: "999999"}},
        },
    )
    # and one that IS ours but belongs to no entry any more
    stale = mirrored.seed_event(CALENDAR, owned_event(unique_user.household_id, 999999, day(2)))

    assert sync_now(api_client, unique_user) == {"created": 0, "updated": 0, "deleted": 1}

    after = {event["id"]: event for event in mirrored.events(CALENDAR)}
    assert stale["id"] not in after
    for untouched in (dentist, neighbours, shared_mark):
        assert after[untouched["id"]] == untouched

    # and it stays that way when the entry itself goes
    unplan(api_client, unique_user, entry["id"])
    after = {event["id"]: event for event in mirrored.events(CALENDAR)}
    assert set(after) == {dentist["id"], neighbours["id"], shared_mark["id"]}
    for untouched in (dentist, neighbours, shared_mark):
        assert after[untouched["id"]] == untouched


def test_dinners_already_eaten_are_left_as_a_record(
    api_client: TestClient, unique_user: TestUser, mirrored: FakeGoogleCalendar
):
    last_week = (window_start() - timedelta(days=7)).isoformat()

    # an event from a past sync whose entry has since been deleted from the plan
    eaten = mirrored.seed_event(CALENDAR, owned_event(unique_user.household_id, 424242, last_week, "Dinner: Chili"))
    # and an entry somebody adds to the past: it is not mirrored
    plan(api_client, unique_user, last_week, title="Backfilled")
    tonight = plan(api_client, unique_user, day(), title="Lentil Tacos")

    assert sync_now(api_client, unique_user) == {"created": 0, "updated": 0, "deleted": 0}

    events = {event["id"]: event for event in mirrored.events(CALENDAR)}
    assert events[eaten["id"]] == eaten
    assert sorted(event["summary"] for event in events.values()) == ["Dinner: Chili", "Dinner: Lentil Tacos"]
    assert tonight["id"]


def test_one_households_mirror_is_invisible_to_anothers(
    api_client: TestClient, unique_user: TestUser, h2_user: TestUser, mirrored: FakeGoogleCalendar
):
    # the hard case: both households mirror into the very same calendar
    configure_as_server(h2_user, CALENDAR, True)

    ours = plan(api_client, unique_user, day(), title="Lentil Tacos")
    theirs = plan(api_client, h2_user, day(), title="Fish Pie")

    def summaries_by_household() -> dict[str, list[str]]:
        found: dict[str, list[str]] = {}
        for event in mirrored.events(CALENDAR):
            found.setdefault(event["extendedProperties"]["private"][HOUSEHOLD_PROPERTY], []).append(event["summary"])
        return found

    assert summaries_by_household() == {
        unique_user.household_id: ["Dinner: Lentil Tacos"],
        h2_user.household_id: ["Dinner: Fish Pie"],
    }

    # each household's sync sees only its own events, so neither finds anything to do
    assert sync_now(api_client, unique_user) == {"created": 0, "updated": 0, "deleted": 0}
    assert sync_now(api_client, h2_user) == {"created": 0, "updated": 0, "deleted": 0}
    # (h2's probe is the one list without the filter; it reads a single event and keeps none)
    filters = {query["privateExtendedProperty"] for query in mirrored.lists() if "privateExtendedProperty" in query}
    assert filters == {
        f"{HOUSEHOLD_PROPERTY}={unique_user.household_id}",
        f"{HOUSEHOLD_PROPERTY}={h2_user.household_id}",
    }

    # one household emptying its plan removes its own event and not the other's
    unplan(api_client, h2_user, theirs["id"])
    assert summaries_by_household() == {unique_user.household_id: ["Dinner: Lentil Tacos"]}
    unplan(api_client, unique_user, ours["id"])
    assert mirrored.events(CALENDAR) == []

    # and the settings are each household's own
    mirrored.add_calendar(OTHER_CALENDAR)
    configure_as_server(h2_user, OTHER_CALENDAR, False)
    assert status(api_client, h2_user)["calendarId"] == OTHER_CALENDAR
    assert status(api_client, h2_user)["enabled"] is False
    assert status(api_client, unique_user)["calendarId"] == CALENDAR
    assert status(api_client, unique_user)["enabled"] is True
    sync_now(api_client, h2_user, expected_status=409)


# ---------------------------------------------------------------------------
# when Google fails


def test_a_google_failure_is_recorded_does_not_fail_the_plan_change_and_heals(
    api_client: TestClient, unique_user: TestUser, mirrored: FakeGoogleCalendar
):
    mirrored.fail_calendar_calls(503)

    # `plan` asserts the 201: the request that changed the plan is answered as if Google were fine
    entry = plan(api_client, unique_user, day(), title="Lentil Tacos")

    failed = status(api_client, unique_user)
    assert failed["lastError"] == "Google Calendar answered 503: Backend Error"
    assert failed["lastSyncedAt"] is None
    assert mirrored.events(CALENDAR) == []

    # asking by hand while it is still down says so, and is not a success
    refused = sync_now(api_client, unique_user, expected_status=502)
    assert "503" in refused["detail"]["message"]

    mirrored.fail_calendar_calls(None)
    assert sync_now(api_client, unique_user) == {"created": 1, "updated": 0, "deleted": 0}

    healed = status(api_client, unique_user)
    assert healed["lastError"] is None
    assert healed["lastSyncedAt"]
    [event] = mirrored.events(CALENDAR)
    assert event["extendedProperties"]["private"][ENTRY_PROPERTY] == str(entry["id"])


def test_google_being_unreachable_is_recorded_and_does_not_fail_the_plan_change(
    api_client: TestClient, unique_user: TestUser, mirrored: FakeGoogleCalendar
):
    mirrored.fail_calendar_calls(httpx.ConnectError("no route to host"))

    entry = plan(api_client, unique_user, day())
    unplan(api_client, unique_user, entry["id"])

    assert status(api_client, unique_user)["lastError"] == "could not reach Google Calendar (ConnectError)"


def test_a_refused_credential_is_recorded_without_any_of_the_key(
    api_client: TestClient, unique_user: TestUser, mirrored: FakeGoogleCalendar
):
    google_credentials.token_cache.clear()  # the probe's token is gone; the next sync must sign in
    mirrored.fail_sign_in(400, "invalid_grant", "Invalid JWT Signature.")

    plan(api_client, unique_user, day())

    response = api_client.get(api_routes.households_calendar_sync, headers=unique_user.token)
    assert response.json()["lastError"] == (
        "Google refused this server's credential (400: invalid_grant: Invalid JWT Signature.)"
    )
    for line in private_key_pem().splitlines():
        assert line not in response.text


@pytest.mark.parametrize(
    ("tz", "named"),
    [
        (None, "TZ is not set"),
        ("", "TZ is not set"),
        ("Not/A_Zone", "TZ is 'Not/A_Zone'"),
        ("MST7MDT,M3.2.0,M11.1.0", "TZ is 'MST7MDT,M3.2.0,M11.1.0'"),
        (" America/Denver ", "TZ is ' America/Denver '"),
    ],
)
def test_a_server_without_a_usable_time_zone_changes_nothing_and_says_why(
    api_client: TestClient,
    unique_user: TestUser,
    mirrored: FakeGoogleCalendar,
    monkeypatch: pytest.MonkeyPatch,
    tz: str | None,
    named: str,
):
    stays = plan(api_client, unique_user, day(), title="Lentil Tacos")
    goes = plan(api_client, unique_user, day(1), title="Fish Pie")
    by_entry = {e["extendedProperties"]["private"][ENTRY_PROPERTY]: e for e in mirrored.events(CALENDAR)}

    # an all-day dinner from an older version: the next sync would give it its hours
    old = by_entry[str(stays["id"])]
    mirrored.calendars[CALENDAR][old["id"]] |= {"start": {"date": day()}, "end": {"date": day(1)}}
    before = mirrored.events(CALENDAR)
    asked = mirrored.request_count

    if tz is None:
        monkeypatch.delenv("TZ")
    else:
        monkeypatch.setenv("TZ", tz)

    # every trigger there is: a plan change that would add an event, one that would delete one,
    # the sweep, and somebody asking. `plan` and `unplan` assert the plan change succeeds.
    added = plan(api_client, unique_user, day(2), title="Chili")
    unplan(api_client, unique_user, goes["id"])
    sync_google_calendars()
    refused = sync_now(api_client, unique_user, expected_status=502)

    said = status(api_client, unique_user)["lastError"]
    assert named in said
    assert "America/Denver" in said, "the page says what TZ should be set to"
    assert refused["detail"]["message"] == said

    # nothing written, patched or deleted, and Google not so much as asked
    assert mirrored.events(CALENDAR) == before
    assert mirrored.request_count == asked

    # with the zone back, the next sync does everything that was held up
    monkeypatch.setenv("TZ", ZONE.key)
    assert sync_now(api_client, unique_user) == {"created": 1, "updated": 1, "deleted": 1}

    assert status(api_client, unique_user)["lastError"] is None
    after = {e["extendedProperties"]["private"][ENTRY_PROPERTY]: e for e in mirrored.events(CALENDAR)}
    assert set(after) == {str(stays["id"]), str(added["id"])}
    assert (after[str(stays["id"])]["start"], after[str(stays["id"])]["end"]) == dinner_time()
    assert (after[str(added["id"])]["start"], after[str(added["id"])]["end"]) == dinner_time(2)


def test_the_sweep_heals_a_sync_that_failed(
    api_client: TestClient, unique_user: TestUser, h2_user: TestUser, mirrored: FakeGoogleCalendar
):
    # h2 has a calendar noted but its mirror is off: the sweep must leave it alone
    mirrored.add_calendar(OTHER_CALENDAR)
    configure_as_server(h2_user, OTHER_CALENDAR, False)
    plan(api_client, h2_user, day(), title="Fish Pie")

    mirrored.fail_calendar_calls(503)
    plan(api_client, unique_user, day(), title="Lentil Tacos")
    assert status(api_client, unique_user)["lastError"]
    mirrored.fail_calendar_calls(None)
    mirrored.calls.clear()

    sync_google_calendars()

    assert [event["summary"] for event in mirrored.events(CALENDAR)] == ["Dinner: Lentil Tacos"]
    assert status(api_client, unique_user)["lastError"] is None
    assert status(api_client, unique_user)["lastSyncedAt"]

    assert mirrored.events(OTHER_CALENDAR) == []
    assert all(path.startswith(CALENDAR) for _, path, _ in mirrored.calls)
    assert status(api_client, h2_user)["lastSyncedAt"] is None


# ---------------------------------------------------------------------------
# the one thing that comes back from Google: the day


def plan_entry(api_client: TestClient, user: TestUser, entry_id: int) -> dict:
    return assert_deserialize(
        api_client.get(api_routes.households_mealplans_item_id(entry_id), headers=user.token), 200
    )


def reschedule(api_client: TestClient, user: TestUser, entry: dict, on: str) -> None:
    """Move an entry to another day in Mealie, through the real endpoint."""

    response = api_client.put(
        api_routes.households_mealplans_item_id(entry["id"]), json={**entry, "date": on}, headers=user.token
    )
    assert response.status_code == 200, response.text


def drag(google: FakeGoogleCalendar, event: dict, offset: int) -> None:
    """Drag a dinner's event to another day in Google Calendar, keeping its hours."""

    start, end = dinner_time(offset)
    google.edit_event(CALENDAR, event["id"], {"start": start, "end": end})


def test_a_dinner_moved_to_another_day_in_google_moves_in_mealie(
    api_client: TestClient, unique_user: TestUser, mirrored: FakeGoogleCalendar
):
    entry = plan(api_client, unique_user, day(), title="Lentil Tacos", text="Double it")
    other = plan(api_client, unique_user, day(1), title="Fish Pie")
    [event, untouched] = mirrored.events(CALENDAR)

    drag(mirrored, event, 2)

    assert sync_now(api_client, unique_user) == {"created": 0, "updated": 1, "deleted": 0}

    after = plan_entry(api_client, unique_user, entry["id"])
    assert after["date"] == day(2)
    # only the day came back: the rest of the entry is as Mealie had it
    assert {k: v for k, v in after.items() if k != "date"} == {k: v for k, v in entry.items() if k != "date"}
    assert plan_entry(api_client, unique_user, other["id"])["date"] == day(1)

    [moved, still] = mirrored.events(CALENDAR)
    assert moved["id"] == event["id"], "the event that was dragged is the entry's event still"
    assert (moved["start"], moved["end"]) == dinner_time(2)
    assert moved["extendedProperties"]["private"][DATE_PROPERTY] == day(2)
    assert still == untouched

    # the move is settled: the next sync finds nothing to do on either side
    assert sync_now(api_client, unique_user) == {"created": 0, "updated": 0, "deleted": 0}
    assert plan_entry(api_client, unique_user, entry["id"])["date"] == day(2)


def test_the_sweep_is_what_picks_up_a_move_nobody_asked_mealie_about(
    api_client: TestClient, unique_user: TestUser, mirrored: FakeGoogleCalendar
):
    entry = plan(api_client, unique_user, day(), title="Lentil Tacos")
    [event] = mirrored.events(CALENDAR)

    drag(mirrored, event, -1)
    sync_google_calendars()

    assert plan_entry(api_client, unique_user, entry["id"])["date"] == day(-1)
    [moved] = mirrored.events(CALENDAR)
    assert (moved["start"], moved["end"]) == dinner_time(-1)


def test_a_meal_dragged_onto_another_day_as_an_all_day_event_moves_and_gets_its_hours_back(
    api_client: TestClient, unique_user: TestUser, mirrored: FakeGoogleCalendar
):
    entry = plan(api_client, unique_user, day(), title="Lentil Tacos")
    [event] = mirrored.events(CALENDAR)

    # dragged into the all-day strip of the day after
    mirrored.edit_event(CALENDAR, event["id"], {"start": {"date": day(1)}, "end": {"date": day(2)}})

    sync_now(api_client, unique_user)

    assert plan_entry(api_client, unique_user, entry["id"])["date"] == day(1)
    [moved] = mirrored.events(CALENDAR)
    assert (moved["start"], moved["end"]) == dinner_time(1), "the day is Google's, the hours are Mealie's"


def test_an_event_nobody_moved_moves_nothing(
    api_client: TestClient, unique_user: TestUser, mirrored: FakeGoogleCalendar
):
    entry = plan(api_client, unique_user, day(), title="Lentil Tacos")
    before = mirrored.events(CALENDAR)
    mirrored.calls.clear()

    assert sync_now(api_client, unique_user) == {"created": 0, "updated": 0, "deleted": 0}

    assert plan_entry(api_client, unique_user, entry["id"]) == entry
    assert mirrored.events(CALENDAR) == before
    assert mirrored.writes() == []


def test_a_new_time_on_the_same_day_changes_nothing_in_mealie(
    api_client: TestClient, unique_user: TestUser, mirrored: FakeGoogleCalendar
):
    entry = plan(api_client, unique_user, day(), title="Lentil Tacos")
    [event] = mirrored.events(CALENDAR)

    # somebody made dinner an hour and a half earlier, and longer
    earlier = datetime.combine(DAY, time(16, 30), tzinfo=ZONE)
    mirrored.edit_event(
        CALENDAR,
        event["id"],
        {
            "start": {"dateTime": earlier.isoformat(), "timeZone": ZONE.key},
            "end": {"dateTime": (earlier + timedelta(hours=4)).isoformat(), "timeZone": ZONE.key},
        },
    )

    result = sync_now(api_client, unique_user)

    assert plan_entry(api_client, unique_user, entry["id"]) == entry
    # and the event is put back to six to eight, as any other drift from the plan is
    assert result["updated"] == 1
    [restored] = mirrored.events(CALENDAR)
    assert (restored["start"], restored["end"]) == dinner_time()


def test_a_new_title_or_description_in_google_changes_nothing_in_mealie(
    api_client: TestClient, unique_user: TestUser, mirrored: FakeGoogleCalendar
):
    entry = plan(api_client, unique_user, day(), title="Lentil Tacos", text="Double it")
    [event] = mirrored.events(CALENDAR)

    mirrored.edit_event(CALENDAR, event["id"], {"summary": "Takeout?", "description": "Too tired"})

    sync_now(api_client, unique_user)

    assert plan_entry(api_client, unique_user, entry["id"]) == entry
    [restored] = mirrored.events(CALENDAR)
    assert restored["summary"] == "Dinner: Lentil Tacos"
    assert restored["description"] == "Double it"


def test_an_event_deleted_in_google_does_not_delete_the_meal(
    api_client: TestClient, unique_user: TestUser, mirrored: FakeGoogleCalendar
):
    entry = plan(api_client, unique_user, day(), title="Lentil Tacos")
    [event] = mirrored.events(CALENDAR)

    # deleted by hand in Google Calendar: Google keeps it as a cancelled event
    mirrored.edit_event(CALENDAR, event["id"], {"status": "cancelled"})
    assert mirrored.events(CALENDAR) == []

    assert sync_now(api_client, unique_user) == {"created": 1, "updated": 0, "deleted": 0}

    assert plan_entry(api_client, unique_user, entry["id"]) == entry
    [rewritten] = mirrored.events(CALENDAR)
    assert rewritten["id"] != event["id"]
    assert rewritten["summary"] == "Dinner: Lentil Tacos"
    assert (rewritten["start"], rewritten["end"]) == dinner_time()


def test_an_event_moved_and_then_deleted_in_google_moves_nothing(
    api_client: TestClient, unique_user: TestUser, mirrored: FakeGoogleCalendar
):
    entry = plan(api_client, unique_user, day(), title="Lentil Tacos")
    [event] = mirrored.events(CALENDAR)

    drag(mirrored, event, 2)
    mirrored.edit_event(CALENDAR, event["id"], {"status": "cancelled"})

    sync_now(api_client, unique_user)

    assert plan_entry(api_client, unique_user, entry["id"])["date"] == day()


def test_a_meal_moved_in_mealie_too_keeps_the_day_mealie_gave_it(
    api_client: TestClient, unique_user: TestUser, mirrored: FakeGoogleCalendar
):
    entry = plan(api_client, unique_user, day(), title="Lentil Tacos")
    [event] = mirrored.events(CALENDAR)

    # Google is down while Mealie moves the meal, so the event is not told; then it is dragged
    mirrored.fail_calendar_calls(503)
    reschedule(api_client, unique_user, entry, day(1))
    mirrored.fail_calendar_calls(None)
    drag(mirrored, event, 2)

    assert sync_now(api_client, unique_user) == {"created": 0, "updated": 1, "deleted": 0}

    assert plan_entry(api_client, unique_user, entry["id"])["date"] == day(1)
    [event] = mirrored.events(CALENDAR)
    assert (event["start"], event["end"]) == dinner_time(1), "the event goes where Mealie put the meal"
    assert event["extendedProperties"]["private"][DATE_PROPERTY] == day(1)


# the day before the window opens: read from the calendar still, mirrored to it no longer
EATEN = -4


def test_a_dinner_dragged_to_before_the_window_moves_there_and_back_again(
    api_client: TestClient, unique_user: TestUser, mirrored: FakeGoogleCalendar
):
    entry = plan(api_client, unique_user, day(), title="Lentil Tacos")
    [event] = mirrored.events(CALENDAR)

    # dragged back to the day it was cooked on
    drag(mirrored, event, EATEN)

    assert sync_now(api_client, unique_user) == {"created": 0, "updated": 1, "deleted": 0}

    assert plan_entry(api_client, unique_user, entry["id"])["date"] == day(EATEN)
    [eaten] = mirrored.events(CALENDAR)
    assert eaten["id"] == event["id"]
    assert (eaten["start"], eaten["end"]) == dinner_time(EATEN)
    assert eaten["extendedProperties"]["private"][DATE_PROPERTY] == day(EATEN), "the event says where Mealie has it"

    # from then on it is a record: nothing more to do
    assert sync_now(api_client, unique_user) == {"created": 0, "updated": 0, "deleted": 0}

    # dragged forward again, to the very day it started on
    drag(mirrored, eaten, 0)

    assert sync_now(api_client, unique_user) == {"created": 0, "updated": 1, "deleted": 0}

    assert plan_entry(api_client, unique_user, entry["id"])["date"] == day()
    [back] = mirrored.events(CALENDAR)
    assert back["id"] == event["id"], "the event is followed, not deleted"
    assert (back["start"], back["end"]) == dinner_time()
    assert back["extendedProperties"]["private"][DATE_PROPERTY] == day()


def test_a_meal_put_back_in_mealie_after_a_drag_to_before_the_window_stays_put(
    api_client: TestClient, unique_user: TestUser, mirrored: FakeGoogleCalendar
):
    entry = plan(api_client, unique_user, day(), title="Lentil Tacos")
    [event] = mirrored.events(CALENDAR)
    drag(mirrored, event, EATEN)
    sync_now(api_client, unique_user)

    # changed in Mealie, which syncs as it happens
    reschedule(api_client, unique_user, entry, day())

    assert plan_entry(api_client, unique_user, entry["id"])["date"] == day()
    [restored] = mirrored.events(CALENDAR)
    assert restored["id"] == event["id"]
    assert (restored["start"], restored["end"]) == dinner_time()
    assert restored["extendedProperties"]["private"][DATE_PROPERTY] == day()

    assert sync_now(api_client, unique_user) == {"created": 0, "updated": 0, "deleted": 0}
    assert plan_entry(api_client, unique_user, entry["id"])["date"] == day()


def test_a_move_to_before_the_window_that_google_was_not_told_about_is_told_by_the_next_sync(
    api_client: TestClient, unique_user: TestUser, mirrored: FakeGoogleCalendar
):
    entry = plan(api_client, unique_user, day(), title="Lentil Tacos")
    [event] = mirrored.events(CALENDAR)

    # where a sync leaves things when it moves the meal and Google then fails: the meal and its
    # event on the same day, and the event still saying the old one
    mirrored.fail_calendar_calls(503)
    reschedule(api_client, unique_user, entry, day(EATEN))
    mirrored.fail_calendar_calls(None)
    drag(mirrored, event, EATEN)

    assert sync_now(api_client, unique_user) == {"created": 0, "updated": 1, "deleted": 0}

    assert plan_entry(api_client, unique_user, entry["id"])["date"] == day(EATEN)
    [eaten] = mirrored.events(CALENDAR)
    assert eaten["id"] == event["id"]
    assert eaten["extendedProperties"]["private"][DATE_PROPERTY] == day(EATEN)


def test_an_event_from_before_moves_were_followed_is_not_read_as_a_move(
    api_client: TestClient, unique_user: TestUser, mirrored: FakeGoogleCalendar
):
    entry = plan(api_client, unique_user, day(), title="Lentil Tacos")
    [event] = mirrored.events(CALENDAR)

    # an event written by the mirror before it kept the day: no record of where Mealie put it,
    # and by now on another day than the entry
    stored = mirrored.calendars[CALENDAR][event["id"]]
    del stored["extendedProperties"]["private"][DATE_PROPERTY]
    drag(mirrored, event, 2)

    assert sync_now(api_client, unique_user) == {"created": 0, "updated": 1, "deleted": 0}

    assert plan_entry(api_client, unique_user, entry["id"])["date"] == day()
    [restored] = mirrored.events(CALENDAR)
    assert (restored["start"], restored["end"]) == dinner_time()
    assert restored["extendedProperties"]["private"][DATE_PROPERTY] == day(), "and from now on it has one"


def test_a_move_in_one_households_calendar_cannot_move_another_households_meal(
    api_client: TestClient, unique_user: TestUser, h2_user: TestUser, mirrored: FakeGoogleCalendar
):
    theirs = plan(api_client, h2_user, day(), title="Fish Pie")

    # an event in this household's mirror that names the other household's entry, moved
    forged = owned_event(unique_user.household_id, theirs["id"], day())
    seeded = mirrored.seed_event(CALENDAR, forged)
    mirrored.edit_event(CALENDAR, seeded["id"], {"start": {"date": day(2)}, "end": {"date": day(3)}})

    sync_now(api_client, unique_user)

    assert plan_entry(api_client, h2_user, theirs["id"])["date"] == day()


# ---------------------------------------------------------------------------
# when there is nothing to do


def test_a_household_with_its_mirror_off_makes_no_google_calls(
    api_client: TestClient, unique_user: TestUser, google: FakeGoogleCalendar
):
    google.add_calendar(CALENDAR)
    save(api_client, unique_user, calendarId=CALENDAR, enabled=False)

    entry = plan(api_client, unique_user, day())
    response = api_client.put(
        api_routes.households_mealplans_item_id(entry["id"]),
        json={**entry, "title": "Changed"},
        headers=unique_user.token,
    )
    assert response.status_code == 200, response.text
    unplan(api_client, unique_user, entry["id"])
    sync_google_calendars()

    refused = sync_now(api_client, unique_user, expected_status=409)
    assert "not turned on" in refused["detail"]["message"]

    # not a sign-in, not a list: nothing
    assert google.request_count == 0
    assert status(api_client, unique_user)["lastSyncedAt"] is None
    assert status(api_client, unique_user)["lastError"] is None


def test_a_household_that_never_set_anything_up_makes_no_google_calls(
    api_client: TestClient, unique_user: TestUser, google: FakeGoogleCalendar
):
    entry = plan(api_client, unique_user, day())
    unplan(api_client, unique_user, entry["id"])
    sync_now(api_client, unique_user, expected_status=409)

    assert google.request_count == 0


def test_a_server_without_credentials_makes_no_google_calls(
    api_client: TestClient, unique_user: TestUser, mirrored: FakeGoogleCalendar, monkeypatch: pytest.MonkeyPatch
):
    # the household is enabled from a time the server had credentials, and now it has none
    monkeypatch.setattr(google_credentials, "load_credentials", lambda: None)
    before = mirrored.request_count

    entry = plan(api_client, unique_user, day())
    unplan(api_client, unique_user, entry["id"])
    sync_google_calendars()

    refused = sync_now(api_client, unique_user, expected_status=409)
    assert "GOOGLE_CALENDAR_CREDENTIALS" in refused["detail"]["message"]

    assert mirrored.request_count == before
    assert status(api_client, unique_user)["configured"] is False
    assert status(api_client, unique_user)["accountEmail"] is None
    # the household's own choice is still there for when the credentials come back
    assert status(api_client, unique_user)["enabled"] is True
