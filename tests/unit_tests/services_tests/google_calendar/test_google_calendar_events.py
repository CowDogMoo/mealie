"""Unit tests for how a meal-plan entry becomes a calendar event, for the reconcile that keeps a
calendar equal to a plan, and for the gate that keeps two reconciles from overlapping."""

import threading
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4

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
)
from mealie.services.household_services.calendar_sync import HouseholdSyncGate, reconcile, window_start
from tests.utils.fake_google_calendar import FakeGoogleCalendar, service_account_credentials

HOUSEHOLD = "0b8a4f6e-3d0c-4d53-9b65-4f3f1e6f2a11"
BASE_URL = "https://mealie.example.test"
CALENDAR = "dinner@group.calendar.google.com"


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
    return event_body(plan_entry, household_id=HOUSEHOLD, base_url=BASE_URL, group_slug="home")


# ---------------------------------------------------------------------------
# one entry, one event


def test_a_recipe_entry_becomes_an_all_day_event_that_blocks_nobodys_time():
    assert body(entry()) == {
        "summary": "Dinner: Lentil Tacos",
        "description": "https://mealie.example.test/g/home/r/lentil-tacos",
        "start": {"date": "2026-11-12"},
        "end": {"date": "2026-11-13"},
        "transparency": "transparent",
        "source": {"title": "Lentil Tacos", "url": "https://mealie.example.test/g/home/r/lentil-tacos"},
        "extendedProperties": {"private": {HOUSEHOLD_PROPERTY: HOUSEHOLD, ENTRY_PROPERTY: "41"}},
    }


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


def test_the_event_ends_the_next_day_across_a_month_and_a_year():
    assert body(entry(on=date(2026, 11, 30)))["end"] == {"date": "2026-12-01"}
    assert body(entry(on=date(2026, 12, 31)))["end"] == {"date": "2027-01-01"}


def test_without_a_group_slug_there_is_no_link_rather_than_a_broken_one():
    event = event_body(entry(), household_id=HOUSEHOLD, base_url=BASE_URL, group_slug=None)

    assert event["description"] == ""
    assert "source" not in event


def test_an_event_belongs_to_a_household_only_by_its_private_property():
    ours = body(entry())
    assert belongs_to(ours, HOUSEHOLD)
    assert not belongs_to(ours, str(uuid4()))
    assert not belongs_to({"summary": "Dinner: Lentil Tacos"}, HOUSEHOLD)
    assert not belongs_to({"extendedProperties": {"shared": {HOUSEHOLD_PROPERTY: HOUSEHOLD}}}, HOUSEHOLD)


def test_an_event_differs_when_its_name_text_or_day_no_longer_match():
    wanted = body(entry(text="Double it"))
    stored = wanted | {"id": "e1", "status": "confirmed"}

    assert not event_differs(stored, wanted)
    # what Google may do to text on the way in and out is not a difference
    assert not event_differs(stored | {"description": wanted["description"].replace("\n", "\r\n") + "\n"}, wanted)
    # nor is anything a person changed that the plan has no opinion about
    assert not event_differs(stored | {"colorId": "5", "transparency": "opaque", "reminders": {}}, wanted)

    assert event_differs(stored | {"summary": "Dinner: Lentil Tacos!"}, wanted)
    assert event_differs(stored | {"description": ""}, wanted)
    assert event_differs(stored | {"start": {"date": "2026-11-13"}}, wanted)
    assert event_differs(stored | {"end": {"date": "2026-11-14"}}, wanted)
    assert event_differs(stored | {"start": {"dateTime": "2026-11-12T18:00:00-07:00"}}, wanted)


def test_a_patch_removes_what_a_plain_patch_would_leave_behind():
    wanted = body(entry(recipe=None, title="Leftovers"))

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


# ---------------------------------------------------------------------------
# a plan and a calendar


def reconciled(google: FakeGoogleCalendar, plan: list[ReadPlanEntry], first_day: date) -> dict[str, int]:
    with google.client() as http:
        client = GoogleCalendarClient(service_account_credentials(), http, AccessTokenCache())
        result = reconcile(client, CALENDAR, HOUSEHOLD, {str(e.id): body(e) for e in plan}, first_day)
    return result.model_dump()


def test_a_timed_event_is_put_back_to_all_day_and_a_dropped_recipe_loses_its_link():
    google = FakeGoogleCalendar()
    google.add_calendar(CALENDAR)
    first_day = date(2026, 11, 11)

    with_recipe = entry()
    assert reconciled(google, [with_recipe], first_day) == {"created": 1, "updated": 0, "deleted": 0}
    [event] = google.events(CALENDAR)

    # somebody drags it to 6pm in Google Calendar...
    google.calendars[CALENDAR][event["id"]] |= {
        "start": {"dateTime": "2026-11-12T18:00:00-07:00", "timeZone": "America/Denver"},
        "end": {"dateTime": "2026-11-12T19:00:00-07:00", "timeZone": "America/Denver"},
    }
    # ...and the plan swaps the recipe for a note
    note = entry(recipe=None, title="Leftovers")

    assert reconciled(google, [note], first_day) == {"created": 0, "updated": 1, "deleted": 0}

    [after] = google.events(CALENDAR)
    assert after["id"] == event["id"]
    assert after["summary"] == "Dinner: Leftovers"
    assert after["start"] == {"date": "2026-11-12"}
    assert after["end"] == {"date": "2026-11-13"}
    assert "source" not in after

    assert reconciled(google, [note], first_day) == {"created": 0, "updated": 0, "deleted": 0}


def test_an_entry_moved_from_before_the_window_into_it_takes_its_event_along():
    google = FakeGoogleCalendar()
    google.add_calendar(CALENDAR)
    first_day = date(2026, 11, 11)

    was = entry(on=date(2026, 11, 10))  # the day before the window
    google.seed_event(CALENDAR, body(was))

    now = entry(on=date(2026, 11, 14))
    assert reconciled(google, [now], first_day) == {"created": 0, "updated": 1, "deleted": 0}

    [event] = google.events(CALENDAR)
    assert event["start"] == {"date": "2026-11-14"}


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
        **event_body(planned, household_id=str(uuid4()), base_url=BASE_URL, group_slug="home"),
        "id": "neighbours",
        "summary": "Dinner: something else entirely",
    }
    unmarked_entry = {**wanted, "id": "half", "extendedProperties": {"private": {ENTRY_PROPERTY: "41"}}}
    ours_but_stale = {**body(entry(entry_id=999)), "id": "stale"}

    calendar = UnfilteredCalendar([somebodys_own, neighbours, unmarked_entry, ours_but_stale])
    result = reconcile(calendar, CALENDAR, HOUSEHOLD, {"41": wanted}, date(2026, 11, 11))  # type: ignore[arg-type]

    # only the event that is ours is removed; the three that are not are neither patched nor
    # deleted, and none of them counts as entry 41's event, so that one is added
    assert calendar.deleted == ["stale"]
    assert calendar.patched == []
    assert calendar.inserted == [wanted]
    assert result.model_dump() == {"created": 1, "updated": 0, "deleted": 1}


def test_the_window_starts_yesterday():
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
