"""Mirror the household's meal plan to a Google calendar.

```
GET    /api/households/calendar-sync         where the mirror stands           (any member)
PUT    /api/households/calendar-sync         which calendar, and on or off     (can manage household)
POST   /api/households/calendar-sync/sync    sync now, and say what changed    (any member)
```

The plan is pushed to the calendar, and the only thing read back is the day of an event somebody
moved in Google Calendar, which moves its plan entry. The Google credential is the server's
(`GOOGLE_CALENDAR_CREDENTIALS`) and no route here returns it or any part of it; `GET` gives only
the address a household shares its calendar with.

The household is fixed by the caller's token through the repository's own filters; no body or
query parameter can name another one.
"""

from functools import cached_property

from fastapi import APIRouter, HTTPException, status

from mealie.routes._base.base_controllers import BaseUserController
from mealie.routes._base.controller import controller
from mealie.schema.household.calendar_sync import CalendarSyncResult, CalendarSyncStatus, CalendarSyncUpdate
from mealie.schema.response.responses import ErrorResponse
from mealie.services.household_services.calendar_sync import (
    CalendarSyncFailedError,
    CalendarSyncNotConfiguredError,
    CalendarSyncNotEnabledError,
    CalendarSyncService,
    CalendarUnreachableError,
)

router = APIRouter(prefix="/households/calendar-sync", tags=["Households: Calendar Sync"])


@controller(router)
class CalendarSyncController(BaseUserController):
    @cached_property
    def service(self) -> CalendarSyncService:
        return CalendarSyncService(self.repos)

    @router.get("", response_model=CalendarSyncStatus)
    def get_status(self) -> CalendarSyncStatus:
        """Whether the server can reach Google, the address to share a calendar with, and this
        household's calendar, switch and latest sync."""

        return self.service.status()

    @router.put("", response_model=CalendarSyncStatus)
    def update_settings(self, data: CalendarSyncUpdate) -> CalendarSyncStatus:
        """Set which calendar this household mirrors to and whether the mirror is on.

        Turning it on checks, there and then, that the calendar can be opened and written to. If
        not, the answer is 400 and names the address the calendar has to be shared with.
        """

        self.checks.can_manage_household()

        try:
            return self.service.configure(data)
        except (CalendarSyncNotConfiguredError, CalendarUnreachableError) as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=ErrorResponse.respond(message=str(e))) from e

    @router.post("/sync", response_model=CalendarSyncResult)
    def sync_now(self) -> CalendarSyncResult:
        """Sync now: follow any event moved to another day in Google Calendar, then make the
        calendar match the plan. Reports how many events that took.

        Refused (409) while the household has not turned its mirror on, or the server has no
        credentials. A sync that fails is a 502, and is also what `lastError` then says: a failure
        on Google's side, or a server whose `TZ` names no time zone.
        """

        try:
            return self.service.sync()
        except (CalendarSyncNotConfiguredError, CalendarSyncNotEnabledError) as e:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=ErrorResponse.respond(message=str(e))) from e
        except CalendarSyncFailedError as e:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=ErrorResponse.respond(message=str(e))) from e
