"""Keeps a household's Google calendar in step with its meal plan as the plan changes.

The meal plan controller already announces every entry it creates, updates or deletes on the event
bus. This listener hears those three events and runs the household's calendar sync, which is a
full reconcile (`mealie.services.household_services.calendar_sync`) and so does not care which
entry changed or how.

It runs where the other listeners run: in a background task, after the response to the request
that changed the plan has been sent. A Google outage therefore cannot fail that request or hold it
up; the failure is written to the household's settings row and the sweep tries again.
"""

from pydantic import UUID4

from mealie.services.google_calendar import credentials as google_credentials
from mealie.services.household_services.calendar_sync import sync_in_background

from .event_bus_listeners import EventListenerBase
from .event_types import Event, EventTypes

MEALPLAN_EVENTS = frozenset(
    {
        EventTypes.mealplan_entry_created,
        EventTypes.mealplan_entry_updated,
        EventTypes.mealplan_entry_deleted,
    }
)


class _NoPublisher:
    """The base class wants a publisher for sending an event somewhere. This listener forwards
    nothing: the event is only its cue to look at the plan."""

    def publish(self, event: Event, notification_urls: list[str]) -> None:
        return None


class CalendarSyncEventListener(EventListenerBase):
    def __init__(self, group_id: UUID4, household_id: UUID4) -> None:
        super().__init__(group_id, household_id, _NoPublisher())

    def get_subscribers(self, event: Event) -> list[UUID4]:
        """The household itself, when this is a plan change and the server can reach Google.

        Both checks are free -- no database, no network -- because this is asked for every event
        on the bus, most of which are not about the meal plan at all. Whether the household has
        its mirror turned on is settled inside the sync, on the session opened for it.
        """

        if event.event_type not in MEALPLAN_EVENTS:
            return []

        if google_credentials.load_credentials() is None:
            return []

        return [self.household_id]

    def publish_to_subscribers(self, event: Event, subscribers: list[UUID4]) -> None:
        # `ensure_repos` is handed over uncalled: the session is opened only once this sync has
        # its turn, so it reads the plan as committed then
        sync_in_background(self.household_id, lambda: self.ensure_repos(self.group_id, self.household_id))
