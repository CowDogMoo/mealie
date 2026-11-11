"""Mirror a household's meal plan to one Google calendar.

**A sync is a reconcile, not a push of one change.** Whatever caused it -- an entry created,
moved or deleted, somebody pressing "Sync now", the hourly sweep -- the work is the same: read the
plan, read the calendar's events that carry this household's mark, and make the second equal the
first. That is why a missed or failed sync costs nothing but time: the next one starts from the
plan again and has no memory to be wrong.

**The window.** Only entries dated from yesterday onward are mirrored, and only events in that
window are ever changed or removed. Older events stay as they were: a record of what was eaten.
"Yesterday" rather than "today" so that a server whose clock is hours ahead of the kitchen does
not stop caring about tonight's dinner in the middle of the afternoon.

**What is never touched.** An event without this household's private property. The calendar is a
shared, human calendar; the mirror owns only what it wrote.

**One at a time.** The planner replaces a week in a burst of about fourteen writes, each of which
asks for a sync. Two reconciles running together would both see an entry as missing and both add
it. So syncs are serialized per household, and while one is already waiting its turn any further
request for that household is dropped: the waiting one reads the plan when it starts, which is
after everything that asked. The lock is in this process only. If two processes ever do overlap,
the reconcile's removal of duplicate events for one entry is what puts it right, on the next run.
"""

import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from dateutil.tz import tzlocal
from pydantic import UUID4

from mealie.core.root_logger import get_logger
from mealie.db.db_setup import session_context
from mealie.repos.all_repositories import get_repositories
from mealie.repos.repository_factory import AllRepositories
from mealie.schema.household.calendar_sync import (
    LAST_ERROR_MAX_LENGTH,
    CalendarSyncOut,
    CalendarSyncResult,
    CalendarSyncSave,
    CalendarSyncStatus,
    CalendarSyncUpdate,
)
from mealie.schema.response.pagination import PaginationQuery
from mealie.services._base_service import BaseService
from mealie.services.google_calendar import client as google_client
from mealie.services.google_calendar import credentials as google_credentials
from mealie.services.google_calendar.client import GoogleCalendarClient, GoogleCalendarError
from mealie.services.google_calendar.credentials import GoogleAuthError, GoogleCredentials
from mealie.services.google_calendar.events import (
    belongs_to,
    entry_id_of,
    event_body,
    event_differs,
    patch_body,
    start_date_of,
)

CREDENTIALS_SETTING = "GOOGLE_CALENDAR_CREDENTIALS"

WINDOW_LOOKBACK_DAYS = 1
"""The mirror covers entries from this many days ago onward. See the module docstring."""

LIST_MARGIN_DAYS = 2
"""How much further back than the window the calendar is read. Google decides what "ends after"
means for an all-day event in the calendar's own time zone, which this server does not know, so
the read is generous and the window is applied here, on each event's date."""

PROBE_MESSAGE_MAX_LENGTH = 1000
"""A bound on what "enable" answers with when a calendar cannot be used, so that nothing Google
says can make the answer unbounded."""

logger = get_logger()


class CalendarSyncError(Exception):
    """Base for the refusals this service raises. The controller maps each to a status code."""


class CalendarSyncNotConfiguredError(CalendarSyncError):
    """The server has no usable Google credential."""


class CalendarSyncNotEnabledError(CalendarSyncError):
    """This household has not turned its mirror on, or has not said which calendar."""


class CalendarUnreachableError(CalendarSyncError):
    """The calendar a household wants to enable cannot be opened, or cannot be written to."""


class CalendarSyncFailedError(CalendarSyncError):
    """A sync was attempted and Google, or the road to it, failed. Already recorded on the row."""


class HouseholdSyncGate:
    """One sync at a time per household, and at most one more waiting behind it."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._queued: set[str] = set()

    def _lock_for(self, key: str) -> threading.Lock:
        return self._locks.setdefault(key, threading.Lock())

    def wait_turn[T](self, key: str, work: Callable[[], T]) -> T:
        """Run `work` once any sync already running for `key` has finished. For a person who asked
        and is waiting for the answer."""

        with self._guard:
            lock = self._lock_for(key)

        with lock:
            return work()

    def run_unless_queued[T](self, key: str, work: Callable[[], T]) -> T | None:
        """Run `work` in turn, unless a sync for `key` is already waiting for its turn.

        In that case nothing is run and None comes back. The waiting sync has not read the plan
        yet, and when it does it will see whatever this request wanted mirrored.
        """

        with self._guard:
            if key in self._queued:
                return None
            self._queued.add(key)
            lock = self._lock_for(key)

        lock.acquire()
        try:
            # no longer waiting: from here a new request has to queue, because this run may
            # already have read the plan
            with self._guard:
                self._queued.discard(key)
            return work()
        finally:
            lock.release()


sync_gate = HouseholdSyncGate()
"""The process-wide gate every trigger goes through."""


def window_start(today: date | None = None) -> date:
    """The first day the mirror covers."""

    today = today or datetime.now(tzlocal()).date()
    return today - timedelta(days=WINDOW_LOOKBACK_DAYS)


def reconcile(
    calendar: GoogleCalendarClient,
    calendar_id: str,
    household_id: str,
    desired: dict[str, dict[str, Any]],
    first_day: date,
) -> CalendarSyncResult:
    """Make the calendar's events for this household equal `desired`, keyed by plan entry id.

    Adds what is missing, patches what no longer matches, and deletes events whose entry is gone
    along with any second event for an entry that already has one.
    """

    ends_after = datetime.combine(first_day - timedelta(days=LIST_MARGIN_DAYS), time.min, tzinfo=UTC)
    listed = calendar.list_events(calendar_id, household_id=household_id, ends_after=ends_after)

    by_entry: dict[str | None, list[dict[str, Any]]] = {}
    for event in listed:
        # Google already filtered on the property. It is checked again here because this loop is
        # what decides which events may be deleted.
        if not belongs_to(event, household_id) or not event.get("id") or event.get("status") == "cancelled":
            continue

        entry_id = entry_id_of(event)
        if entry_id not in desired and (start_date_of(event) or "") < first_day.isoformat():
            # before the window and not wanted in it: a dinner that was eaten. Leave it be.
            continue

        by_entry.setdefault(entry_id, []).append(event)

    result = CalendarSyncResult()

    for entry_id, events in by_entry.items():
        wanted = desired.get(entry_id) if entry_id else None
        if wanted is None:
            for event in events:
                calendar.delete_event(calendar_id, event["id"])
                result.deleted += 1
            continue

        # the oldest event is the one kept, so repeated runs agree on which copy is the duplicate
        keeper, *duplicates = sorted(events, key=lambda e: (str(e.get("created") or ""), str(e["id"])))
        for event in duplicates:
            calendar.delete_event(calendar_id, event["id"])
            result.deleted += 1

        if event_differs(keeper, wanted):
            calendar.patch_event(calendar_id, keeper["id"], patch_body(keeper, wanted))
            result.updated += 1

    for entry_id, wanted in desired.items():
        if entry_id not in by_entry:
            calendar.insert_event(calendar_id, wanted)
            result.created += 1

    return result


def _one_line(text: str, limit: int = LAST_ERROR_MAX_LENGTH) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class CalendarSyncService(BaseService):
    """A household's calendar mirror: its settings, its status, and the sync itself.

    `repos` must be household-scoped. The repository's own group and household filters are the
    whole access boundary here -- the settings row and the plan entries are both found through
    them -- so a group-wide `AllRepositories` is refused rather than allowed to read whichever
    household's row comes back first.
    """

    def __init__(self, repos: AllRepositories) -> None:
        super().__init__()
        if not repos.group_id or not repos.household_id:
            raise ValueError("CalendarSyncService needs household-scoped repositories")

        self.repos = repos
        self.rows = repos.calendar_sync
        self.household_id = str(repos.household_id)

    # ------------------------------------------------------------------ settings

    def _row(self) -> CalendarSyncOut | None:
        found = self.rows.multi_query({}, limit=1)
        return found[0] if found else None

    def status(self) -> CalendarSyncStatus:
        credentials = google_credentials.load_credentials()
        row = self._row()

        return CalendarSyncStatus(
            configured=credentials is not None,
            account_email=credentials.client_email if credentials else None,
            calendar_id=row.calendar_id if row else None,
            enabled=bool(row and row.enabled),
            last_synced_at=row.last_synced_at if row else None,
            last_error=row.last_error if row else None,
        )

    def configure(self, data: CalendarSyncUpdate) -> CalendarSyncStatus:
        """Save which calendar to mirror to and whether the mirror is on.

        Turning it on is refused unless the calendar can be opened and written to right now, so
        a wrong id or a calendar nobody shared is found out on this page and not an hour later.
        Saving with the mirror off talks to nobody.
        """

        if data.enabled:
            credentials = google_credentials.load_credentials()
            if credentials is None:
                raise CalendarSyncNotConfiguredError(
                    f"This server has no Google credentials yet. Whoever runs it needs to set {CREDENTIALS_SETTING} "
                    "before calendar sync can be turned on."
                )
            if not data.calendar_id:
                raise CalendarUnreachableError("Enter the Calendar ID of the Google calendar to sync to.")

            self._probe(credentials, data.calendar_id)

        row = self._row()
        if row is None:
            self.rows.create(
                CalendarSyncSave(
                    calendar_id=data.calendar_id,
                    enabled=data.enabled,
                    group_id=self.repos.group_id,  # type: ignore[arg-type]
                    household_id=self.repos.household_id,  # type: ignore[arg-type]
                )
            )
        else:
            changes: dict[str, Any] = {"calendar_id": data.calendar_id, "enabled": data.enabled}
            if data.calendar_id != row.calendar_id:
                # what happened on the old calendar says nothing about the new one
                changes |= {"last_synced_at": None, "last_error": None}
            self.rows.patch(row.id, changes)

        return self.status()

    def _probe(self, credentials: GoogleCredentials, calendar_id: str) -> None:
        """Refuse a calendar that cannot be opened and written to, in words somebody can act on.

        Every refusal names the address the calendar has to be shared with. They are answers to a
        person on the settings page, not rows in a table, so they are not cut to `last_error`'s
        length: a service account's address alone can run to eighty characters.
        """

        who = credentials.client_email
        share = f'In Google Calendar, share the calendar with {who} and choose "Make changes to events"'

        try:
            with google_client.open_client(credentials) as calendar:
                role = calendar.access_role(calendar_id)
        except GoogleAuthError as e:
            raise CalendarUnreachableError(
                _one_line(f"Mealie could not sign in to Google as {who}: {e}.", PROBE_MESSAGE_MAX_LENGTH)
            ) from e
        except GoogleCalendarError as e:
            raise CalendarUnreachableError(
                _one_line(
                    f"Mealie could not open that calendar. {share}, then check the Calendar ID. ({e})",
                    PROBE_MESSAGE_MAX_LENGTH,
                )
            ) from e

        if role is not None and role not in google_client.WRITER_ROLES:
            raise CalendarUnreachableError(f"{who} can see that calendar but cannot change it. {share}.")

    # ------------------------------------------------------------------ syncing

    def _ready(self) -> tuple[CalendarSyncOut, GoogleCredentials]:
        credentials = google_credentials.load_credentials()
        if credentials is None:
            raise CalendarSyncNotConfiguredError(
                f"This server has no Google credentials yet ({CREDENTIALS_SETTING} is not set)."
            )

        row = self._row()
        if row is None or not row.enabled or not row.calendar_id:
            raise CalendarSyncNotEnabledError("Calendar sync is not turned on for this household.")

        return row, credentials

    def sync(self) -> CalendarSyncResult:
        """Sync now, for a person waiting on the answer. Waits for a running sync to finish first.

        Raises when the household or the server is not set up, and when Google fails.
        """

        row, credentials = self._ready()
        return sync_gate.wait_turn(self.household_id, lambda: self._sync(row, credentials))

    def sync_if_enabled(self) -> CalendarSyncResult | None:
        """Sync if this household mirrors its plan, and never raise over Google.

        For the triggers nobody is waiting on. A household that is not set up is simply skipped,
        without a call to Google; a failure is recorded on the row and reported as None. The
        caller is responsible for the gate (see `sync_in_background`).
        """

        try:
            row, credentials = self._ready()
        except (CalendarSyncNotConfiguredError, CalendarSyncNotEnabledError):
            return None

        try:
            return self._sync(row, credentials)
        except CalendarSyncFailedError:
            return None

    def _desired_events(self, first_day: date) -> dict[str, dict[str, Any]]:
        entries = self.repos.meals.page_all(
            PaginationQuery(page=1, per_page=-1, query_filter=f"date >= {first_day.isoformat()}")
        ).items

        group = self.repos.groups.get_one(self.repos.group_id)  # type: ignore[arg-type]
        group_slug = group.slug if group else None

        return {
            str(entry.id): event_body(
                entry, household_id=self.household_id, base_url=self.settings.BASE_URL, group_slug=group_slug
            )
            for entry in entries
        }

    def _sync(self, row: CalendarSyncOut, credentials: GoogleCredentials) -> CalendarSyncResult:
        try:
            first_day = window_start()
            desired = self._desired_events(first_day)
            with google_client.open_client(credentials) as calendar:
                result = reconcile(calendar, row.calendar_id or "", self.household_id, desired, first_day)
        except (GoogleAuthError, GoogleCalendarError) as e:
            reason = _one_line(str(e))
            self.logger.warning("calendar sync failed for household %s: %s", self.household_id, reason)
            self.rows.patch(row.id, {"last_error": reason})
            raise CalendarSyncFailedError(reason) from e
        except Exception as e:
            # a fault of Mealie's own. It still belongs on the settings page: a mirror that has
            # quietly stopped must not go on saying "last synced" as if nothing were wrong.
            reason = f"The sync stopped on an unexpected error ({type(e).__name__}); the server log has the details."
            self.logger.exception("calendar sync for household %s stopped on an unexpected error", self.household_id)
            self.rows.patch(row.id, {"last_error": reason})
            raise CalendarSyncFailedError(reason) from e

        self.rows.patch(row.id, {"last_synced_at": datetime.now(UTC), "last_error": None})
        self.logger.debug(
            "calendar sync for household %s: %d created, %d updated, %d deleted",
            self.household_id,
            result.created,
            result.updated,
            result.deleted,
        )
        return result


@contextmanager
def household_repos(group_id: UUID4, household_id: UUID4) -> Iterator[AllRepositories]:
    """Household-scoped repositories on a session of their own, closed on the way out."""

    with session_context() as session:
        yield get_repositories(session, group_id=group_id, household_id=household_id)


def sync_in_background(
    household_id: UUID4 | str, open_repos: Callable[[], AbstractContextManager[AllRepositories]]
) -> CalendarSyncResult | None:
    """Sync one household for a trigger nobody is waiting on: a plan change, or the hourly sweep.

    Goes through the gate, so it is dropped (None) when a sync for the household is already
    waiting. `open_repos` is called only once this run has its turn, so the session it opens is
    new and reads the plan as it is committed at that moment, not as it was when the trigger
    fired. Never raises: whatever goes wrong here must not reach the request, or the scheduler
    loop, that set it off.
    """

    def work() -> CalendarSyncResult | None:
        with open_repos() as repos:
            return CalendarSyncService(repos).sync_if_enabled()

    try:
        return sync_gate.run_unless_queued(str(household_id), work)
    except Exception:
        logger.exception("calendar sync for household %s stopped on an unexpected error", household_id)
        return None
