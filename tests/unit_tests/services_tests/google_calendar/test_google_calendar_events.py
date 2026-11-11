"""Unit tests for how a meal-plan entry becomes a calendar event, for the reconcile that keeps a
calendar equal to a plan, and for the gate that keeps two reconciles from overlapping."""

import threading
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from mealie.schema.meal_plan.new_meal import ReadPlanEntry
from mealie.schema.recipe.recipe import RecipeSummary
from mealie.services.google_calendar.client import GoogleCalendarClient
from mealie.services.google_calendar.credentials import AccessTokenCache
from mealie.services.google_calendar.events import (
    ENTRY_PROPERTY,
    HOUSEHOLD_PROPERTY,
    belongs_to,
    event_body,
    event_differs,
    patch_body,
    start_date_of,
)
from mealie.services.household_services.calendar_sync import (
    CalendarSyncTimeZoneError,
    HouseholdSyncGate,
    reconcile,
    server_zone,
    window_start,
)
from tests.utils.fake_google_calendar import FakeGoogleCalendar, service_account_credentials

HOUSEHOLD = "0b8a4f6e-3d0c-4d53-9b65-4f3f1e6f2a11"
BASE_URL = "https://mealie.example.test"
CALENDAR = "dinner@group.calendar.google.com"
DENVER = ZoneInfo("America/Denver")


def entry(
    entry_id: int = 41, on: date = date(2026, 11, 12), recipe: str | None = "Lentil Tacos", **fields
) -> ReadPlanEntry:
    summary = RecipeSummary(name=recipe, slug=recipe.lower().replace(" ", "-")) if recipe else None
    return ReadPlanEntry(
        id=entry_id,
        date=on,
        entry_type=fields.pop("entry_type", "dinner"),
        title=fields.pop("title", "" if recipe else "Leftovers"),
        text=fields.pop("text", ""),
        recipe_id=uuid4() if recipe else None,
        group_id=uuid4(),
        user_id=uuid4(),
        household_id=HOUSEHOLD,
        recipe=summary,
    )


def body(plan_entry: ReadPlanEntry) -> dict[str, Any]:
    return event_body(plan_entry, household_id=HOUSEHOLD, base_url=BASE_URL, group_slug="home", zone=DENVER)


# ---------------------------------------------------------------------------
# one entry, one event


def test_a_dinner_becomes_an_event_from_six_to_eight_that_blocks_nobodys_time():
    assert body(entry()) == {
        "summary": "Dinner: Lentil Tacos",
        "description": "https://mealie.example.test/g/home/r/lentil-tacos",
        "start": {"dateTime": "2026-11-12T18:00:00-07:00", "timeZone": "America/Denver"},
        "end": {"dateTime": "2026-11-12T20:00:00-07:00", "timeZone": "America/Denver"},
        "transparency": "transparent",
        "source": {"title": "Lentil Tacos", "url": "https://mealie.example.test/g/home/r/lentil-tacos"},
        "extendedProperties": {"private": {HOUSEHOLD_PROPERTY: HOUSEHOLD, ENTRY_PROPERTY: "41"}},
    }


@pytest.mark.parametrize(
    ("on", "offset"),
    [
        (date(2026, 10, 31), "-06:00"),  # the last evening of daylight time
        (date(2026, 11, 1), "-07:00"),  # the clocks went back that morning
        (date(2027, 3, 13), "-07:00"),  # the last evening of standard time
        (date(2027, 3, 14), "-06:00"),  # the clocks went forward that morning
        (date(2026, 7, 4), "-06:00"),
    ],
)
def test_dinner_is_at_six_on_the_kitchen_clock_on_either_side_of_a_daylight_saving_change(on: date, offset: str):
    event = body(entry(on=on))

    assert event["start"] == {"dateTime": f"{on.isoformat()}T18:00:00{offset}", "timeZone": "America/Denver"}
    assert event["end"] == {"dateTime": f"{on.isoformat()}T20:00:00{offset}", "timeZone": "America/Denver"}


@pytest.mark.parametrize("kind", ["breakfast", "lunch", "side", "snack", "drink", "dessert"])
def test_every_other_kind_of_meal_is_still_an_all_day_event(kind: str):
    event = body(entry(entry_type=kind))

    assert event["summary"] == f"{kind.capitalize()}: Lentil Tacos"
    assert event["start"] == {"date": "2026-11-12"}
    assert event["end"] == {"date": "2026-11-13"}
    assert event["transparency"] == "transparent"
    assert event["extendedProperties"] == {"private": {HOUSEHOLD_PROPERTY: HOUSEHOLD, ENTRY_PROPERTY: "41"}}


def test_an_entry_that_is_only_a_note_uses_its_title_and_links_nowhere():
    event = body(entry(recipe=None, title="Leftovers", text="Whatever is in the fridge", entry_type="lunch"))

    assert event["summary"] == "Lunch: Leftovers"
    assert event["description"] == "Whatever is in the fridge"
    assert "source" not in event


def test_the_entrys_text_comes_before_the_recipe_link():
    event = body(entry(text="  Double it, Sam is coming  "))

    assert event["description"] == "Double it, Sam is coming\n\nhttps://mealie.example.test/g/home/r/lentil-tacos"


def test_the_recipes_name_wins_over_a_title_and_a_bare_entry_is_just_its_meal():
    assert body(entry(title="ignored"))["summary"] == "Dinner: Lentil Tacos"

    bare = entry(recipe=None, title="x")
    bare.title = "  "
    assert body(bare)["summary"] == "Dinner"


def test_an_all_day_event_ends_the_next_day_across_a_month_and_a_year():
    assert body(entry(on=date(2026, 11, 30), entry_type="lunch"))["end"] == {"date": "2026-12-01"}
    assert body(entry(on=date(2026, 12, 31), entry_type="lunch"))["end"] == {"date": "2027-01-01"}


def test_without_a_group_slug_there_is_no_link_rather_than_a_broken_one():
    event = event_body(entry(), household_id=HOUSEHOLD, base_url=BASE_URL, group_slug=None, zone=DENVER)

    assert event["description"] == ""
    assert "source" not in event


def test_an_event_belongs_to_a_household_only_by_its_private_property():
    ours = body(entry())
    assert belongs_to(ours, HOUSEHOLD)
    assert not belongs_to(ours, str(uuid4()))
    assert not belongs_to({"summary": "Dinner: Lentil Tacos"}, HOUSEHOLD)
    assert not belongs_to({"extendedProperties": {"shared": {HOUSEHOLD_PROPERTY: HOUSEHOLD}}}, HOUSEHOLD)


def test_an_all_day_event_differs_when_its_name_text_or_day_no_longer_match():
    wanted = body(entry(text="Double it", entry_type="lunch"))
    stored = wanted | {"id": "e1", "status": "confirmed"}

    assert not event_differs(stored, wanted)
    # what Google may do to text on the way in and out is not a difference
    assert not event_differs(stored | {"description": wanted["description"].replace("\n", "\r\n") + "\n"}, wanted)
    # nor is anything a person changed that the plan has no opinion about
    assert not event_differs(stored | {"colorId": "5", "transparency": "opaque", "reminders": {}}, wanted)

    assert event_differs(stored | {"summary": "Lunch: Lentil Tacos!"}, wanted)
    assert event_differs(stored | {"description": ""}, wanted)
    assert event_differs(stored | {"start": {"date": "2026-11-13"}}, wanted)
    assert event_differs(stored | {"end": {"date": "2026-11-14"}}, wanted)
    assert event_differs(stored | {"start": {"dateTime": "2026-11-12T18:00:00-07:00"}}, wanted)


def test_a_dinner_differs_by_the_moment_it_is_at_and_not_by_how_google_writes_it():
    wanted = body(entry())
    stored = wanted | {"id": "e1", "status": "confirmed"}

    assert not event_differs(stored, wanted)
    # the same two moments, as a calendar kept in another zone hands them back
    elsewhere = {
        "start": {"dateTime": "2026-11-13T01:00:00Z", "timeZone": "Europe/London"},
        "end": {"dateTime": "2026-11-13T03:00:00+00:00", "timeZone": "Europe/London"},
    }
    assert not event_differs(stored | elsewhere, wanted)

    # a dinner still written the old way, as a whole day
    assert event_differs(stored | {"start": {"date": "2026-11-12"}, "end": {"date": "2026-11-13"}}, wanted)
    # dragged to another hour, or stretched
    assert event_differs(stored | {"start": {"dateTime": "2026-11-12T17:00:00-07:00"}}, wanted)
    assert event_differs(stored | {"end": {"dateTime": "2026-11-12T19:00:00-07:00"}}, wanted)
    # another day at the same hour
    assert event_differs(stored | {"start": {"dateTime": "2026-11-13T18:00:00-07:00"}}, wanted)
    # nothing that can be read as a moment
    assert event_differs(stored | {"start": {"dateTime": "six-ish"}}, wanted)
    assert event_differs(stored | {"start": {"dateTime": "2026-11-12T18:00:00"}}, wanted)
    assert event_differs(stored | {"start": {}}, wanted)


def test_a_patch_removes_what_a_plain_patch_would_leave_behind():
    wanted = body(entry(recipe=None, title="Leftovers", entry_type="lunch"))

    plain = patch_body(wanted | {"id": "e1"}, wanted)
    assert plain == wanted

    timed = {
        "id": "e1",
        "start": {"dateTime": "2026-11-12T18:00:00-07:00", "timeZone": "America/Denver"},
        "end": {"dateTime": "2026-11-12T19:00:00-07:00", "timeZone": "America/Denver"},
        "source": {"title": "Lentil Tacos", "url": "https://mealie.example.test/g/home/r/lentil-tacos"},
    }
    patch = patch_body(timed, wanted)
    assert patch["start"] == {"date": "2026-11-12", "dateTime": None, "timeZone": None}
    assert patch["end"] == {"date": "2026-11-13", "dateTime": None, "timeZone": None}
    assert patch["source"] is None
    # the caller's copy of what is wanted is not what got the nulls
    assert wanted["start"] == {"date": "2026-11-12"}


def test_a_patch_takes_the_date_off_an_all_day_dinner_when_it_gives_it_an_hour():
    wanted = body(entry())
    all_day = wanted | {"id": "e1", "start": {"date": "2026-11-12"}, "end": {"date": "2026-11-13"}}

    patch = patch_body(all_day, wanted)
    assert patch["start"] == {"date": None, "dateTime": "2026-11-12T18:00:00-07:00", "timeZone": "America/Denver"}
    assert patch["end"] == {"date": None, "dateTime": "2026-11-12T20:00:00-07:00", "timeZone": "America/Denver"}
    assert patch["extendedProperties"] == wanted["extendedProperties"]
    assert patch["transparency"] == "transparent"

    # a dinner that already has its hour is patched without any nulls
    assert patch_body(wanted | {"id": "e1"}, wanted) == wanted


def test_a_timed_events_day_is_read_in_the_zone_it_was_written_in():
    assert start_date_of({"start": {"date": "2026-11-12"}}, DENVER) == "2026-11-12"
    # six in the evening in Denver, handed back by a calendar kept in UTC
    assert start_date_of({"start": {"dateTime": "2026-11-13T01:00:00Z"}}, DENVER) == "2026-11-12"
    assert (
        start_date_of({"start": {"dateTime": "2026-11-12T18:00:00-07:00"}}, ZoneInfo("Europe/London")) == "2026-11-13"
    )
    # nothing that can be read as a moment: the date it opens with is all there is to go on
    assert start_date_of({"start": {"dateTime": "2026-11-13 six-ish"}}, DENVER) == "2026-11-13"
    assert start_date_of({"start": {"dateTime": "2026-11-13T01:00:00"}}, DENVER) == "2026-11-13"
    assert start_date_of({}, DENVER) is None


# ---------------------------------------------------------------------------
# a plan and a calendar


def reconciled(google: FakeGoogleCalendar, plan: list[ReadPlanEntry], first_day: date) -> dict[str, int]:
    with google.client() as http:
        client = GoogleCalendarClient(service_account_credentials(), http, AccessTokenCache())
        result = reconcile(client, CALENDAR, HOUSEHOLD, {str(e.id): body(e) for e in plan}, first_day, DENVER)
    return result.model_dump()


def test_a_timed_event_is_put_back_to_all_day_and_a_dropped_recipe_loses_its_link():
    google = FakeGoogleCalendar()
    google.add_calendar(CALENDAR)
    first_day = date(2026, 11, 11)

    with_recipe = entry(entry_type="lunch")
    assert reconciled(google, [with_recipe], first_day) == {"created": 1, "updated": 0, "deleted": 0}
    [event] = google.events(CALENDAR)

    # somebody drags it to 6pm in Google Calendar...
    google.calendars[CALENDAR][event["id"]] |= {
        "start": {"dateTime": "2026-11-12T18:00:00-07:00", "timeZone": "America/Denver"},
        "end": {"dateTime": "2026-11-12T19:00:00-07:00", "timeZone": "America/Denver"},
    }
    # ...and the plan swaps the recipe for a note
    note = entry(recipe=None, title="Leftovers", entry_type="lunch")

    assert reconciled(google, [note], first_day) == {"created": 0, "updated": 1, "deleted": 0}

    [after] = google.events(CALENDAR)
    assert after["id"] == event["id"]
    assert after["summary"] == "Lunch: Leftovers"
    assert after["start"] == {"date": "2026-11-12"}
    assert after["end"] == {"date": "2026-11-13"}
    assert "source" not in after

    assert reconciled(google, [note], first_day) == {"created": 0, "updated": 0, "deleted": 0}


def old_all_day_body(plan_entry: ReadPlanEntry) -> dict[str, Any]:
    """A dinner's event as it was written before dinners had an hour: the whole day."""

    return body(plan_entry) | {
        "start": {"date": plan_entry.date.isoformat()},
        "end": {"date": (plan_entry.date + timedelta(days=1)).isoformat()},
    }


def test_an_all_day_dinner_from_an_older_sync_is_given_its_hour_in_place():
    google = FakeGoogleCalendar()
    google.add_calendar(CALENDAR)
    first_day = date(2026, 11, 11)

    dinner = entry()
    lunch = entry(entry_id=42, entry_type="lunch")
    old = google.seed_event(CALENDAR, old_all_day_body(dinner))
    google.seed_event(CALENDAR, body(lunch))

    assert reconciled(google, [dinner, lunch], first_day) == {"created": 0, "updated": 1, "deleted": 0}

    events = {e["extendedProperties"]["private"][ENTRY_PROPERTY]: e for e in google.events(CALENDAR)}
    assert len(google.events(CALENDAR)) == 2, "no second event for the dinner"

    converted = events["41"]
    assert converted["id"] == old["id"]
    assert converted["start"] == {"dateTime": "2026-11-12T18:00:00-07:00", "timeZone": "America/Denver"}
    assert converted["end"] == {"dateTime": "2026-11-12T20:00:00-07:00", "timeZone": "America/Denver"}
    assert converted["transparency"] == "transparent"
    assert belongs_to(converted, HOUSEHOLD)

    assert events["42"]["start"] == {"date": "2026-11-12"}

    # and the timed event is recognised as the entry's own from then on
    assert reconciled(google, [dinner, lunch], first_day) == {"created": 0, "updated": 0, "deleted": 0}


def test_a_dinner_handed_back_in_the_calendars_own_zone_is_left_alone():
    google = FakeGoogleCalendar()
    google.add_calendar(CALENDAR)
    first_day = date(2026, 11, 11)

    dinner = entry()
    assert reconciled(google, [dinner], first_day) == {"created": 1, "updated": 0, "deleted": 0}
    [event] = google.events(CALENDAR)

    # Google answers in the calendar's zone, whatever zone the event was written in
    google.calendars[CALENDAR][event["id"]] |= {
        "start": {"dateTime": "2026-11-13T01:00:00Z", "timeZone": "UTC"},
        "end": {"dateTime": "2026-11-13T03:00:00Z", "timeZone": "UTC"},
    }

    assert reconciled(google, [dinner], first_day) == {"created": 0, "updated": 0, "deleted": 0}


def test_an_eaten_dinner_is_a_record_even_when_the_calendar_puts_its_evening_on_the_next_day():
    google = FakeGoogleCalendar()
    google.add_calendar(CALENDAR)
    first_day = date(2026, 11, 11)

    eaten = google.seed_event(
        CALENDAR,
        body(entry(entry_id=7, on=date(2026, 11, 10)))
        # six in the evening on the 10th in Denver is one in the morning on the 11th in UTC
        | {"start": {"dateTime": "2026-11-11T01:00:00Z"}, "end": {"dateTime": "2026-11-11T03:00:00Z"}},
    )

    assert reconciled(google, [], first_day) == {"created": 0, "updated": 0, "deleted": 0}
    assert [event["id"] for event in google.events(CALENDAR)] == [eaten["id"]]


def test_an_entry_moved_from_before_the_window_into_it_takes_its_event_along():
    google = FakeGoogleCalendar()
    google.add_calendar(CALENDAR)
    first_day = date(2026, 11, 11)

    was = entry(on=date(2026, 11, 10))  # the day before the window
    google.seed_event(CALENDAR, body(was))

    now = entry(on=date(2026, 11, 14))
    assert reconciled(google, [now], first_day) == {"created": 0, "updated": 1, "deleted": 0}

    [event] = google.events(CALENDAR)
    assert event["start"] == {"dateTime": "2026-11-14T18:00:00-07:00", "timeZone": "America/Denver"}


class UnfilteredCalendar:
    """A calendar whose list ignores the property filter, as if Google had handed back everything.

    It never should. `reconcile` checks each event's property itself all the same, because that
    check is the only thing between a sync and somebody's own events, and this is what proves it.
    """

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events
        self.inserted: list[dict[str, Any]] = []
        self.patched: list[str] = []
        self.deleted: list[str] = []

    def list_events(self, calendar_id: str, *, household_id: str, ends_after: datetime) -> list[dict[str, Any]]:
        return [dict(event) for event in self.events]

    def insert_event(self, calendar_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self.inserted.append(body)
        return body

    def patch_event(self, calendar_id: str, event_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self.patched.append(event_id)
        return body

    def delete_event(self, calendar_id: str, event_id: str) -> None:
        self.deleted.append(event_id)


def test_reconcile_itself_refuses_to_touch_an_event_without_this_households_mark():
    planned = entry()
    wanted = body(planned)
    day = wanted["start"]

    somebodys_own = {"id": "own", "summary": "Dinner: Lentil Tacos", "start": day, "end": wanted["end"]}
    neighbours = {
        **event_body(planned, household_id=str(uuid4()), base_url=BASE_URL, group_slug="home", zone=DENVER),
        "id": "neighbours",
        "summary": "Dinner: something else entirely",
    }
    unmarked_entry = {**wanted, "id": "half", "extendedProperties": {"private": {ENTRY_PROPERTY: "41"}}}
    ours_but_stale = {**body(entry(entry_id=999)), "id": "stale"}

    calendar = UnfilteredCalendar([somebodys_own, neighbours, unmarked_entry, ours_but_stale])
    result = reconcile(calendar, CALENDAR, HOUSEHOLD, {"41": wanted}, date(2026, 11, 11), DENVER)  # type: ignore[arg-type]

    # only the event that is ours is removed; the three that are not are neither patched nor
    # deleted, and none of them counts as entry 41's event, so that one is added
    assert calendar.deleted == ["stale"]
    assert calendar.patched == []
    assert calendar.inserted == [wanted]
    assert result.model_dump() == {"created": 1, "updated": 0, "deleted": 1}


@pytest.mark.parametrize("named", ["America/Denver", ":America/Denver"])
def test_the_servers_zone_is_the_one_tz_names(monkeypatch: pytest.MonkeyPatch, named: str):
    monkeypatch.setenv("TZ", named)

    assert server_zone() == DENVER


@pytest.mark.parametrize("unset", [None, "", ":"])
def test_a_server_without_a_tz_has_no_zone_and_says_so(monkeypatch: pytest.MonkeyPatch, unset: str | None):
    if unset is None:
        monkeypatch.delenv("TZ", raising=False)
    else:
        monkeypatch.setenv("TZ", unset)

    with pytest.raises(CalendarSyncTimeZoneError) as refused:
        server_zone()
    assert "TZ is not set" in str(refused.value)
    assert "America/Denver" in str(refused.value)

    # and the window, which starts on the server's own day, is not guessed at either
    with pytest.raises(CalendarSyncTimeZoneError):
        window_start()


@pytest.mark.parametrize(
    "invalid",
    [
        "Not/A_Zone",
        "America/Denvr",
        "MST7MDT,M3.2.0,M11.1.0",
        "/etc/localtime",
        " America/Denver ",
        "America/Denver ",
        " ",
    ],
)
def test_a_tz_that_names_no_zone_is_refused_by_name(monkeypatch: pytest.MonkeyPatch, invalid: str):
    monkeypatch.setenv("TZ", invalid)

    with pytest.raises(CalendarSyncTimeZoneError) as refused:
        server_zone()
    assert f"TZ is '{invalid}'" in str(refused.value)
    assert "not a time zone" in str(refused.value)

    with pytest.raises(CalendarSyncTimeZoneError):
        window_start()


def test_the_window_starts_yesterday(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TZ", "America/Denver")

    assert window_start(date(2026, 11, 10)) == date(2026, 11, 9)
    assert window_start(date(2027, 1, 1)) == date(2026, 12, 31)

    today = datetime.now(UTC).date()
    assert abs((window_start() - (today - timedelta(days=1))).days) <= 1  # the server's own day, not UTC's


# ---------------------------------------------------------------------------
# one at a time


def test_a_burst_of_requests_runs_two_syncs_not_one_per_request():
    """The planner replaces a week with about fourteen writes. The first sync is running, the
    second waits for it, and every request after that is dropped because the one waiting has not
    read the plan yet and will see what they wanted mirrored."""

    gate = HouseholdSyncGate()
    first_is_running = threading.Event()
    let_first_finish = threading.Event()
    ran: list[str] = []

    def first() -> str:
        first_is_running.set()
        assert let_first_finish.wait(timeout=5)
        ran.append("first")
        return "first"

    def later() -> str:
        ran.append("later")
        return "later"

    results: dict[str, str | None] = {}
    running = threading.Thread(target=lambda: results.update(first=gate.run_unless_queued("h1", first)))
    running.start()
    assert first_is_running.wait(timeout=5)

    waiting = threading.Thread(target=lambda: results.update(second=gate.run_unless_queued("h1", later)))
    waiting.start()
    # the second request is queued once it shows up in the gate's own bookkeeping
    for _ in range(500):
        if "h1" in gate._queued:
            break
        threading.Event().wait(0.01)
    assert "h1" in gate._queued

    # twelve more arrive while one is waiting: none of them runs, none of them blocks
    assert [gate.run_unless_queued("h1", later) for _ in range(12)] == [None] * 12

    # another household is not held up by this one
    assert gate.run_unless_queued("h2", lambda: "other household") == "other household"

    let_first_finish.set()
    running.join(timeout=5)
    waiting.join(timeout=5)

    assert ran == ["first", "later"]
    assert results == {"first": "first", "second": "later"}

    # and the gate is open again afterwards
    assert gate.run_unless_queued("h1", later) == "later"


def test_a_person_who_asks_waits_their_turn_instead_of_being_dropped():
    gate = HouseholdSyncGate()
    first_is_running = threading.Event()
    let_first_finish = threading.Event()
    order: list[str] = []

    def first() -> None:
        first_is_running.set()
        assert let_first_finish.wait(timeout=5)
        order.append("background")

    background = threading.Thread(target=lambda: gate.run_unless_queued("h1", first))
    background.start()
    assert first_is_running.wait(timeout=5)

    asked: dict[str, str] = {}
    person = threading.Thread(
        target=lambda: asked.update(answer=gate.wait_turn("h1", lambda: order.append("person") or "done"))
    )
    person.start()
    person.join(timeout=0.2)
    assert person.is_alive(), "the manual sync must wait for the running one, not overlap it"

    let_first_finish.set()
    background.join(timeout=5)
    person.join(timeout=5)

    assert order == ["background", "person"]
    assert asked == {"answer": "done"}


def test_a_sync_that_raises_still_opens_the_gate():
    gate = HouseholdSyncGate()

    def boom() -> None:
        raise RuntimeError("google fell over")

    for attempt in (gate.run_unless_queued, gate.wait_turn):
        try:
            attempt("h1", boom)
        except RuntimeError:
            pass

    assert gate.run_unless_queued("h1", lambda: "fine") == "fine"
    assert gate._queued == set()
