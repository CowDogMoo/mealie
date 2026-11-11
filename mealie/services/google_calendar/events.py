"""How one meal-plan entry becomes one Google Calendar event, and how to tell whose an event is.

An event belongs to a household's mirror when it carries that household's id in a *private
extended property* -- a key/value pair Google stores on the event, hides from the calendar UI and
lets the API filter on. A second property names the plan entry. Those two are the whole link
between a plan and a calendar: nothing in Mealie's database remembers an event id, so the link
cannot go stale, and an event without the household's property is by definition somebody's own
and is never read as ours.
"""

from datetime import timedelta
from typing import Any

from mealie.schema.meal_plan.new_meal import ReadPlanEntry
from mealie.services.urls.url_constructors import recipe_url

HOUSEHOLD_PROPERTY = "mealieHousehold"
"""Private extended property holding the id of the household whose plan the event mirrors."""

ENTRY_PROPERTY = "mealieEntry"
"""Private extended property holding the id of the plan entry the event mirrors."""

TRANSPARENT = "transparent"
"""Google's word for "does not block time": dinner is on the calendar, nobody is busy during it."""


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


def event_body(entry: ReadPlanEntry, *, household_id: str, base_url: str, group_slug: str | None) -> dict[str, Any]:
    """The event a plan entry should be: all day, on the entry's date, blocking nobody's time."""

    link = recipe_link(entry, base_url, group_slug)
    description = "\n\n".join(part for part in ((entry.text or "").strip(), link) if part)

    body: dict[str, Any] = {
        "summary": event_summary(entry),
        "description": description,
        # an all-day event runs from its date up to, not including, the next one
        "start": {"date": entry.date.isoformat()},
        "end": {"date": (entry.date + timedelta(days=1)).isoformat()},
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


def start_date_of(event: dict[str, Any]) -> str | None:
    """The date of an all-day event, or the day a timed one starts on."""

    start = event.get("start") or {}
    if start.get("date"):
        return str(start["date"])
    if start.get("dateTime"):
        return str(start["dateTime"])[:10]
    return None


def _text(value: Any) -> str:
    return str(value or "").replace("\r\n", "\n").strip()


def event_differs(existing: dict[str, Any], desired: dict[str, Any]) -> bool:
    """Whether an event no longer says what its plan entry says: its name, its text or its day."""

    return (
        _text(existing.get("summary")) != _text(desired.get("summary"))
        or _text(existing.get("description")) != _text(desired.get("description"))
        or (existing.get("start") or {}).get("date") != desired["start"]["date"]
        or (existing.get("end") or {}).get("date") != desired["end"]["date"]
    )


def patch_body(existing: dict[str, Any], desired: dict[str, Any]) -> dict[str, Any]:
    """What to send to make `existing` say what `desired` says.

    A patch only replaces the fields it names, so two leftovers have to be removed by name, which
    the Calendar API does when a field is sent as null: the clock time of an event somebody turned
    from all-day into a timed one, and the recipe link of an entry that no longer has a recipe.
    """

    body: dict[str, Any] = {**desired, "start": dict(desired["start"]), "end": dict(desired["end"])}

    for edge in ("start", "end"):
        if (existing.get(edge) or {}).get("dateTime"):
            body[edge] |= {"dateTime": None, "timeZone": None}

    if existing.get("source") and "source" not in desired:
        body["source"] = None

    return body
