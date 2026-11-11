"""How one meal-plan entry becomes one Google Calendar event, and how to tell whose an event is.

An event belongs to a household's mirror when it carries that household's id in a *private
extended property* -- a key/value pair Google stores on the event, hides from the calendar UI and
lets the API filter on. A second property names the plan entry. Those two are the whole link
between a plan and a calendar: nothing in Mealie's database remembers an event id, so the link
cannot go stale, and an event without the household's property is by definition somebody's own
and is never read as ours.
"""

from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

from mealie.schema.meal_plan.new_meal import PlanEntryType, ReadPlanEntry
from mealie.services.urls.url_constructors import recipe_url

HOUSEHOLD_PROPERTY = "mealieHousehold"
"""Private extended property holding the id of the household whose plan the event mirrors."""

ENTRY_PROPERTY = "mealieEntry"
"""Private extended property holding the id of the plan entry the event mirrors."""

TRANSPARENT = "transparent"
"""Google's word for "does not block time": dinner is on the calendar, nobody is busy during it."""

DINNER_START = time(18, 0)
DINNER_END = time(20, 0)
"""When a dinner is on the calendar: six to eight in the evening, by the server's clock. Every
other kind of meal has no hour of its own and is an all-day event."""

EDGE_FIELDS = ("date", "dateTime", "timeZone")
"""What an event's start or end can be made of: a date for an all-day event, or a clock time and
the name of the zone it was written in."""


def recipe_link(entry: ReadPlanEntry, base_url: str, group_slug: str | None) -> str | None:
    """Where the entry's recipe lives in Mealie, or None for an entry that is only a note."""

    if entry.recipe is None or not entry.recipe.slug or not group_slug:
        return None

    return recipe_url(group_slug, entry.recipe.slug, base_url)


def event_summary(entry: ReadPlanEntry) -> str:
    """ "Dinner: Lentil Tacos" -- the kind of meal, then the recipe's name or the entry's own title."""

    kind = str(entry.entry_type.value).capitalize()
    what = (entry.recipe.name if entry.recipe and entry.recipe.name else entry.title or "").strip()
    return f"{kind}: {what}" if what else kind


def _edge(day: date, at: time, zone: ZoneInfo) -> dict[str, str]:
    """One end of a timed event: the wall-clock time on that day, with the offset the zone has then.

    The offset is worked out for the day itself, so an event on either side of a daylight-saving
    change is at the same hour on the kitchen clock. The zone is named too.
    """

    return {"dateTime": datetime.combine(day, at, tzinfo=zone).isoformat(), "timeZone": zone.key}


def event_times(entry: ReadPlanEntry, zone: ZoneInfo) -> tuple[dict[str, str], dict[str, str]]:
    """When an entry's event starts and ends: six to eight for a dinner, the whole day otherwise."""

    if entry.entry_type == PlanEntryType.dinner:
        return _edge(entry.date, DINNER_START, zone), _edge(entry.date, DINNER_END, zone)

    # an all-day event runs from its date up to, not including, the next one
    return {"date": entry.date.isoformat()}, {"date": (entry.date + timedelta(days=1)).isoformat()}


def event_body(
    entry: ReadPlanEntry, *, household_id: str, base_url: str, group_slug: str | None, zone: ZoneInfo
) -> dict[str, Any]:
    """The event a plan entry should be: on the entry's date, blocking nobody's time.

    `zone` is the time zone the household keeps its clock in, which is the server's.
    """

    link = recipe_link(entry, base_url, group_slug)
    description = "\n\n".join(part for part in ((entry.text or "").strip(), link) if part)
    start, end = event_times(entry, zone)

    body: dict[str, Any] = {
        "summary": event_summary(entry),
        "description": description,
        "start": start,
        "end": end,
        "transparency": TRANSPARENT,
        "extendedProperties": {"private": {HOUSEHOLD_PROPERTY: household_id, ENTRY_PROPERTY: str(entry.id)}},
    }
    if link:
        body["source"] = {"title": entry.recipe.name if entry.recipe and entry.recipe.name else "Mealie", "url": link}

    return body


def private_properties(event: dict[str, Any]) -> dict[str, Any]:
    properties = (event.get("extendedProperties") or {}).get("private")
    return properties if isinstance(properties, dict) else {}


def belongs_to(event: dict[str, Any], household_id: str) -> bool:
    """Whether an event is part of this household's mirror. Anything else is never touched."""

    return private_properties(event).get(HOUSEHOLD_PROPERTY) == household_id


def entry_id_of(event: dict[str, Any]) -> str | None:
    value = private_properties(event).get(ENTRY_PROPERTY)
    return str(value) if value else None


def _instant(value: Any) -> datetime | None:
    """The moment a `dateTime` names, or None when it is missing or names none."""

    try:
        moment = datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None

    return moment if moment is not None and moment.tzinfo is not None else None


def start_date_of(event: dict[str, Any], zone: tzinfo) -> str | None:
    """The date of an all-day event, or the day a timed one starts on in `zone`.

    Google hands a timed event back in the calendar's own zone, which need not be the server's: six
    in the evening in Denver is already tomorrow in London. The day is read in `zone`, so it is the
    day of the plan entry the event was written for. A `dateTime` that names no moment is read by
    the date it opens with.
    """

    start = event.get("start") or {}
    if start.get("date"):
        return str(start["date"])
    if start.get("dateTime"):
        moment = _instant(start["dateTime"])
        if moment is None:
            return str(start["dateTime"])[:10]
        return moment.astimezone(zone).date().isoformat()
    return None


def _text(value: Any) -> str:
    return str(value or "").replace("\r\n", "\n").strip()


def _edge_differs(existing: dict[str, Any], desired: dict[str, Any]) -> bool:
    """Whether one end of an event is at another time than wanted.

    An all-day end is compared by its date. A timed one is compared as a moment and not as text,
    because Google returns the same moment written in the calendar's zone and offset.
    """

    if "dateTime" in desired:
        moment = _instant(existing.get("dateTime"))
        return existing.get("date") is not None or moment is None or moment != _instant(desired["dateTime"])

    return existing.get("dateTime") is not None or existing.get("date") != desired["date"]


def event_differs(existing: dict[str, Any], desired: dict[str, Any]) -> bool:
    """Whether an event no longer says what its plan entry says: its name, its text or its time."""

    return (
        _text(existing.get("summary")) != _text(desired.get("summary"))
        or _text(existing.get("description")) != _text(desired.get("description"))
        or _edge_differs(existing.get("start") or {}, desired["start"])
        or _edge_differs(existing.get("end") or {}, desired["end"])
    )


def patch_body(existing: dict[str, Any], desired: dict[str, Any]) -> dict[str, Any]:
    """What to send to make `existing` say what `desired` says.

    A patch only replaces the fields it names, so leftovers have to be removed by name, which the
    Calendar API does when a field is sent as null: the date of an all-day event that is becoming
    a timed one (a dinner written before dinners had an hour), the clock time of a timed event
    that is becoming all-day, and the recipe link of an entry that no longer has a recipe.
    """

    body: dict[str, Any] = {**desired, "start": dict(desired["start"]), "end": dict(desired["end"])}

    for edge in ("start", "end"):
        was = existing.get(edge) or {}
        body[edge] |= {field: None for field in EDGE_FIELDS if was.get(field) and field not in desired[edge]}

    if existing.get("source") and "source" not in desired:
        body["source"] = None

    return body
