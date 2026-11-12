"""The four Calendar API calls the meal-plan mirror makes: list, insert, patch and delete events.

Written against the REST API directly (`https://www.googleapis.com/calendar/v3`) with `httpx`,
because four calls do not justify Google's client libraries in the image.

`build_http_client` is the only place an HTTP client for Google is made, for both the sign-in and
the calendar calls. Tests replace it with one whose transport is an in-memory calendar, which is
why nothing here builds a client any other way.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx

from .credentials import AccessTokenCache, GoogleCredentials, token_cache
from .events import HOUSEHOLD_PROPERTY

CALENDAR_API = "https://www.googleapis.com/calendar/v3"

HTTP_TIMEOUT_SECONDS = 15.0
"""Per request. A sync set off by a plan change or the sweep runs on a background thread,
after the response that caused it, so there this bounds how long that thread waits on Google.
"Sync now" and the check made when a mirror is switched on are answered while a person waits, and
for those two it also bounds each request that person is waiting behind."""

PAGE_SIZE = 250
MAX_PAGES = 40
"""A household's mirror is a few dozen events. Forty pages is 10,000, and a list that is still
paging after that is a fault to stop on rather than a calendar to keep reading."""

WRITER_ROLES = frozenset({"owner", "writer", "writerWithoutPrivateAccess"})
"""The access roles that can change events. "Make changes to events" in the sharing dialog is
`writer`; `reader` and `freeBusyReader` can list a calendar and change nothing on it."""


class GoogleCalendarError(Exception):
    """The Calendar API refused a call, or could not be reached."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def build_http_client() -> httpx.Client:
    return httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)


def _segment(value: str) -> str:
    """A calendar or event id as one URL path segment. Calendar ids contain `@` and may contain `#`."""

    return quote(value, safe="")


def _refusal(response: httpx.Response) -> str:
    """Google's own sentence for an error, e.g. `Not Found`, when the body carries one."""

    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        return ""

    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    return ""


class GoogleCalendarClient:
    def __init__(
        self, credentials: GoogleCredentials, http: httpx.Client, tokens: AccessTokenCache | None = None
    ) -> None:
        self._credentials = credentials
        self._http = http
        self._tokens = tokens if tokens is not None else token_cache

    def _send(
        self, method: str, path: str, params: dict[str, str | int] | None, body: dict[str, Any] | None
    ) -> httpx.Response:
        token = self._tokens.get(self._credentials, self._http)
        try:
            return self._http.request(
                method,
                f"{CALENDAR_API}{path}",
                params=params,
                json=body,
                headers={"Authorization": f"Bearer {token}"},
            )
        except (httpx.HTTPError, httpx.InvalidURL) as e:
            raise GoogleCalendarError(f"could not reach Google Calendar ({type(e).__name__})") from e

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
        body: dict[str, Any] | None = None,
        missing_is_fine: bool = False,
    ) -> dict[str, Any]:
        response = self._send(method, path, params, body)
        if response.status_code == 401:
            # a token Google no longer accepts is dropped and asked for again, once
            self._tokens.forget(self._credentials)
            response = self._send(method, path, params, body)

        if missing_is_fine and response.status_code in (404, 410):
            return {}

        if response.status_code >= 400:
            refusal = _refusal(response)
            raise GoogleCalendarError(
                f"Google Calendar answered {response.status_code}{': ' + refusal if refusal else ''}",
                status_code=response.status_code,
            )

        if not response.content:
            return {}

        try:
            data = response.json()
        except ValueError:
            raise GoogleCalendarError("Google Calendar sent an answer that was not JSON") from None

        return data if isinstance(data, dict) else {}

    def access_role(self, calendar_id: str) -> str | None:
        """What this credential may do on a calendar, learned from the cheapest call the sync makes.

        Listing one event needs exactly the access a sync's own listing needs, so a calendar that
        was never shared fails here the way it would fail there (404), and the answer carries the
        credential's role on the calendar, which says whether it could also write.
        """

        page = self._request("GET", f"/calendars/{_segment(calendar_id)}/events", params={"maxResults": 1})
        role = page.get("accessRole")
        return role if isinstance(role, str) else None

    def list_events(self, calendar_id: str, *, household_id: str, ends_after: datetime) -> list[dict[str, Any]]:
        """Every event on the calendar that carries this household's property and ends after a time.

        The filter is Google's, on the private extended property, so other people's events are
        never even downloaded.
        """

        params: dict[str, str | int] = {
            "privateExtendedProperty": f"{HOUSEHOLD_PROPERTY}={household_id}",
            "timeMin": ends_after.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "maxResults": PAGE_SIZE,
            "showDeleted": "false",
        }

        events: list[dict[str, Any]] = []
        for _ in range(MAX_PAGES):
            page = self._request("GET", f"/calendars/{_segment(calendar_id)}/events", params=params)
            events.extend(item for item in page.get("items") or [] if isinstance(item, dict))

            next_page = page.get("nextPageToken")
            if not next_page:
                return events
            params = {**params, "pageToken": str(next_page)}

        raise GoogleCalendarError(f"Google Calendar was still paging after {MAX_PAGES} pages of events")

    def insert_event(self, calendar_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"/calendars/{_segment(calendar_id)}/events", body=body)

    def patch_event(self, calendar_id: str, event_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("PATCH", f"/calendars/{_segment(calendar_id)}/events/{_segment(event_id)}", body=body)

    def delete_event(self, calendar_id: str, event_id: str) -> None:
        # an event that is already gone is the outcome that was wanted
        self._request("DELETE", f"/calendars/{_segment(calendar_id)}/events/{_segment(event_id)}", missing_is_fine=True)


@contextmanager
def open_client(credentials: GoogleCredentials) -> Iterator[GoogleCalendarClient]:
    """A calendar client for one piece of work, with its connections closed afterwards."""

    http = build_http_client()
    try:
        yield GoogleCalendarClient(credentials, http)
    finally:
        http.close()
