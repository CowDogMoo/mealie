"""Hourly: reconcile every household that mirrors its meal plan to Google Calendar.

Plan changes sync as they happen, so on a good day this finds nothing to do. It is here for the
other days: a sync that failed while Google was unreachable, a pod that restarted between a plan
change and its background task, and the changes nothing announces on the event bus -- a recipe
renamed, or deleted out from under its plan entry.
"""

from functools import partial

from mealie.db.db_setup import session_context
from mealie.repos.all_repositories import get_repositories
from mealie.services.google_calendar import credentials as google_credentials
from mealie.services.household_services.calendar_sync import household_repos, sync_in_background


def sync_google_calendars() -> None:
    if google_credentials.load_credentials() is None:
        return

    with session_context() as session:
        # no group and no household: this is the one reader that wants every household's row
        everyone = get_repositories(session, group_id=None, household_id=None)
        rows = everyone.calendar_sync.multi_query({"enabled": True})

    for row in rows:
        # each household on a session of its own, opened once its turn at the gate comes
        sync_in_background(row.household_id, partial(household_repos, row.group_id, row.household_id))
