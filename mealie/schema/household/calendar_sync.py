"""The household's meal plan, mirrored to one Google calendar.

Mealie's plan is the truth -- the planner, Home Assistant and the voting UI all read it -- and the
calendar is a copy of it for people who would rather look at Google Calendar than open Mealie.
One thing only comes back from the calendar into the plan: the day. An event of the mirror's that
somebody moves to another day in Google Calendar moves its plan entry to that day. Its time, title
and description are the plan's to say, and deleting it there deletes nothing here.

It is a push through the Calendar API rather than an ICS feed for two reasons that are both about
this household: the instance is reachable only on the LAN, so Google could not fetch a feed, and a
dinner is often swapped minutes after a thumbs-down, which a feed polled twice a day would show
wrong until the next morning.

Two kinds of settings are involved and they live in different places. The Google credential is
server-wide (`GOOGLE_CALENDAR_CREDENTIALS`) and never appears in any model here. Which calendar a
household mirrors to, and whether the mirror is on, is this module.
"""

from datetime import datetime
from typing import Annotated

from pydantic import UUID4, ConfigDict, StringConstraints, field_validator

from mealie.schema._mealie import MealieModel
from mealie.schema._mealie.mealie_model import UpdatedAtField

CALENDAR_ID_MAX_LENGTH = 1024
"""A calendar id is an address-shaped string (`...@group.calendar.google.com`, or a person's own
email address for their primary calendar). This is far more room than either needs."""

LAST_ERROR_MAX_LENGTH = 300
"""`last_error` is shown on the settings page as one line, so it is kept to roughly that."""

_CalendarIdStr = Annotated[str, StringConstraints(strip_whitespace=True, max_length=CALENDAR_ID_MAX_LENGTH)]


class CalendarSyncUpdate(MealieModel):
    """What the settings page saves: which calendar, and whether to mirror the plan to it."""

    calendar_id: _CalendarIdStr | None = None
    enabled: bool = False

    @field_validator("calendar_id", mode="after")
    @classmethod
    def blank_calendar_is_none(cls, value: str | None) -> str | None:
        return value or None


class CalendarSyncSave(CalendarSyncUpdate):
    group_id: UUID4
    household_id: UUID4
    last_synced_at: datetime | None = None
    last_error: str | None = None


class CalendarSyncOut(CalendarSyncSave):
    """The stored row. Internal: the API answers with `CalendarSyncStatus`, which adds what the
    server knows about its own credential."""

    id: UUID4
    created_at: datetime | None = None
    updated_at: datetime | None = UpdatedAtField(None)

    model_config = ConfigDict(from_attributes=True)


class CalendarSyncStatus(MealieModel):
    """Everything the settings page needs to say where the mirror stands."""

    configured: bool = False
    """Whether the server has a usable Google credential. Without one nothing can be enabled."""

    account_email: str | None = None
    """The address the household shares its calendar with: the service account's `client_email`.
    The credential itself is never returned, only this address."""

    calendar_id: str | None = None
    enabled: bool = False
    last_synced_at: datetime | None = None
    last_error: str | None = None
    """Why the latest sync failed, in a sentence. Cleared by the next sync that works."""


class CalendarSyncResult(MealieModel):
    """What one sync changed on the calendar. All zeroes means it already matched the plan."""

    created: int = 0
    updated: int = 0
    deleted: int = 0
