"""An in-memory Google for the calendar-sync tests: one token endpoint and any number of calendars.

Nothing in the test suite may reach the real Google. This stands in for it behind an
`httpx.MockTransport`, and implements the parts of the Calendar API the sync relies on the way
Google documents them: `events.list` with the `privateExtendedProperty` filter, `timeMin` and
paging; `insert`; `patch` with merge semantics, where a null removes a field; and `delete`, which
answers 410 for an event that is already gone. A calendar that was never added answers 404, which
is what Google says about a calendar nobody shared with the caller.

`service_account_info()` is a real-shaped service-account key around a throwaway RSA key generated
on first use. It signs real JWTs; it opens nothing.
"""

import copy
import json
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any
from urllib.parse import parse_qs, unquote
from uuid import uuid4

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from mealie.services.google_calendar.credentials import GOOGLE_TOKEN_URI, GoogleCredentials, parse_credentials

API_PREFIX = "/calendar/v3/calendars/"
WRITERS = {"owner", "writer", "writerWithoutPrivateAccess"}

SERVICE_ACCOUNT_EMAIL = "mealie-sync@dinner-test.iam.gserviceaccount.com"


@lru_cache(maxsize=1)
def _throwaway_key() -> tuple[str, str]:
    """(private PEM, public PEM) for a key that exists only for the length of the test run."""

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return private_pem, public_pem


def private_key_pem() -> str:
    return _throwaway_key()[0]


def public_key_pem() -> str:
    return _throwaway_key()[1]


def service_account_info() -> dict[str, str]:
    """What a downloaded service-account key file holds, minus the fields nothing here reads."""

    return {
        "type": "service_account",
        "project_id": "dinner-test",
        "private_key_id": "test-key-1",
        "private_key": private_key_pem(),
        "client_email": SERVICE_ACCOUNT_EMAIL,
        "client_id": "100000000000000000001",
        "token_uri": GOOGLE_TOKEN_URI,
    }


def service_account_credentials() -> GoogleCredentials:
    return parse_credentials(json.dumps(service_account_info()))


def _error(status_code: int, message: str) -> httpx.Response:
    return httpx.Response(status_code, json={"error": {"code": status_code, "message": message}})


def _merge(target: dict[str, Any], patch: dict[str, Any]) -> None:
    """Merge-patch: a null removes the field, an object is merged into, anything else replaces."""

    for key, value in patch.items():
        if value is None:
            target.pop(key, None)
        elif isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


def _ends_at(event: dict[str, Any]) -> datetime:
    end = event.get("end") or {}
    if end.get("date"):
        return datetime.fromisoformat(end["date"]).replace(tzinfo=UTC)
    return datetime.fromisoformat(str(end.get("dateTime", "9999-01-01T00:00:00+00:00")).replace("Z", "+00:00"))


class FakeGoogleCalendar:
    def __init__(self) -> None:
        self.calendars: dict[str, dict[str, dict[str, Any]]] = {}
        self.roles: dict[str, str] = {}

        self.token_requests: list[dict[str, str]] = []
        """The form fields of every sign-in, in order."""

        self.calls: list[tuple[str, str, dict[str, str]]] = []
        """Every Calendar API call that got past sign-in: (method, decoded path, query)."""

        self.token_lifetime = 3600
        self.page_size: int | None = None
        """Set to force small pages out of `events.list`, whatever `maxResults` asks for."""

        self._issued: set[str] = set()
        self._created = datetime(2026, 1, 1, tzinfo=UTC)
        self._calendar_failure: int | Exception | None = None
        self._token_failure: tuple[int, dict[str, str]] | None = None

    # ------------------------------------------------------------------ arranging

    def add_calendar(self, calendar_id: str, role: str = "writer") -> None:
        self.calendars.setdefault(calendar_id, {})
        self.roles[calendar_id] = role

    def seed_event(self, calendar_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Put an event on a calendar the way somebody other than the sync would have."""

        return copy.deepcopy(self._store(calendar_id, body))

    def edit_event(self, calendar_id: str, event_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        """Change an event the way a person in Google Calendar would: dragging it, renaming it.

        The fields named are replaced whole; everything else (the private properties included) is
        left as it was.
        """

        event = self.calendars[calendar_id][event_id]
        event |= copy.deepcopy(changes)
        return copy.deepcopy(event)

    def fail_calendar_calls(self, failure: int | Exception | None) -> None:
        """Make every Calendar API call answer this status, or raise this error. None heals it."""

        self._calendar_failure = failure

    def fail_sign_in(self, status_code: int = 400, error: str = "invalid_grant", description: str = "") -> None:
        self._token_failure = (status_code, {"error": error, "error_description": description})

    def revoke_tokens(self) -> None:
        """Forget every token handed out, so the next call carrying one answers 401."""

        self._issued.clear()

    # ------------------------------------------------------------------ observing

    def events(self, calendar_id: str) -> list[dict[str, Any]]:
        """The events on a calendar now, oldest first, without the ones that were deleted."""

        live = [e for e in self.calendars.get(calendar_id, {}).values() if e.get("status") != "cancelled"]
        return copy.deepcopy(sorted(live, key=lambda e: e["created"]))

    def writes(self) -> list[tuple[str, str]]:
        return [(method, path) for method, path, _ in self.calls if method != "GET"]

    def lists(self) -> list[dict[str, str]]:
        return [query for method, _, query in self.calls if method == "GET"]

    @property
    def request_count(self) -> int:
        return len(self.token_requests) + len(self.calls)

    # ------------------------------------------------------------------ serving

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))

    def handle(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == GOOGLE_TOKEN_URI:
            return self._sign_in(request)

        raw_path = request.url.raw_path.decode().split("?", 1)[0]
        if not raw_path.startswith(API_PREFIX):
            return _error(404, "Not Found")

        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if token not in self._issued:
            return _error(401, "Request had invalid authentication credentials.")

        # ids are percent-encoded one path segment each; a client that forgot would split here
        segments = [unquote(segment) for segment in raw_path[len(API_PREFIX) :].split("/")]
        self.calls.append((request.method, "/".join(segments), dict(request.url.params)))

        if isinstance(self._calendar_failure, Exception):
            raise self._calendar_failure
        if self._calendar_failure is not None:
            return _error(self._calendar_failure, "Backend Error")

        if len(segments) not in (2, 3) or segments[1] != "events":
            return _error(404, "Not Found")

        calendar_id = segments[0]
        if calendar_id not in self.calendars or self.roles.get(calendar_id) in (None, "none"):
            return _error(404, "Not Found")

        if len(segments) == 2 and request.method == "GET":
            return self._list(calendar_id, request)

        if self.roles[calendar_id] not in WRITERS:
            return _error(403, "You need to have writer access to this calendar.")

        if len(segments) == 2 and request.method == "POST":
            return self._insert(calendar_id, request)
        if len(segments) == 3 and request.method == "PATCH":
            return self._patch(calendar_id, segments[2], request)
        if len(segments) == 3 and request.method == "DELETE":
            return self._delete(calendar_id, segments[2])

        return _error(404, "Not Found")

    def _sign_in(self, request: httpx.Request) -> httpx.Response:
        form = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
        self.token_requests.append(form)

        if self._token_failure is not None:
            status_code, body = self._token_failure
            return httpx.Response(status_code, json=body)

        token = f"ya29.fake-{len(self.token_requests)}"
        self._issued.add(token)
        return httpx.Response(
            200, json={"access_token": token, "expires_in": self.token_lifetime, "token_type": "Bearer"}
        )

    def _store(self, calendar_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self._created += timedelta(seconds=1)
        event = copy.deepcopy(body)
        event |= {
            "id": uuid4().hex,
            "status": "confirmed",
            "created": self._created.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        }
        self.calendars.setdefault(calendar_id, {})[event["id"]] = event
        return event

    def _list(self, calendar_id: str, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        wanted = [constraint.split("=", 1) for constraint in params.get_list("privateExtendedProperty")]
        show_deleted = params.get("showDeleted") == "true"
        ends_after = datetime.fromisoformat(params["timeMin"].replace("Z", "+00:00")) if "timeMin" in params else None

        def matches(event: dict[str, Any]) -> bool:
            private = (event.get("extendedProperties") or {}).get("private") or {}
            return (
                (show_deleted or event.get("status") != "cancelled")
                and all(private.get(name) == value for name, value in wanted)
                and (ends_after is None or _ends_at(event) > ends_after)
            )

        found = sorted((e for e in self.calendars[calendar_id].values() if matches(e)), key=lambda e: e["created"])

        size = int(params.get("maxResults", 250))
        if self.page_size is not None:
            size = min(size, self.page_size)
        offset = int(params.get("pageToken", 0))

        page: dict[str, Any] = {
            "kind": "calendar#events",
            "accessRole": self.roles[calendar_id],
            "items": copy.deepcopy(found[offset : offset + size]),
        }
        if offset + size < len(found):
            page["nextPageToken"] = str(offset + size)

        return httpx.Response(200, json=page)

    def _insert(self, calendar_id: str, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if not body.get("start") or not body.get("end"):
            return _error(400, "Missing end time.")

        return httpx.Response(200, json=self._store(calendar_id, body))

    def _patch(self, calendar_id: str, event_id: str, request: httpx.Request) -> httpx.Response:
        event = self.calendars[calendar_id].get(event_id)
        if event is None or event.get("status") == "cancelled":
            return _error(404, "Not Found")

        _merge(event, json.loads(request.content))
        return httpx.Response(200, json=event)

    def _delete(self, calendar_id: str, event_id: str) -> httpx.Response:
        event = self.calendars[calendar_id].get(event_id)
        if event is None:
            return _error(404, "Not Found")
        if event.get("status") == "cancelled":
            return _error(410, "Resource has been deleted")

        event["status"] = "cancelled"
        return httpx.Response(204)
