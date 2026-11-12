"""The server's Google credential, and the access tokens minted from it.

`GOOGLE_CALENDAR_CREDENTIALS` holds one JSON document: a **service-account key**
(`"type": "service_account"`). The server signs a short-lived JWT with the key and trades it for
an access token. The service account is its own Google identity with an email address, and a
household shares its calendar with that address.

The trade is one form POST, so it is done here with `httpx` and `PyJWT` rather than by adding
Google's client libraries to the image.

**Nothing in this module may put the credential, or any part of it, into a log line, an exception
message or a return value** other than the access token handed to the caller. The private key
is kept out of `repr`, and the one error that would otherwise quote a key parser is replaced with
a fixed sentence.
"""

import hashlib
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Final

import httpx
import jwt

from mealie.core.config import get_app_settings
from mealie.core.root_logger import get_logger

GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"

CALENDAR_EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"
"""Read and write events, and nothing else: not the calendar list, not sharing. Listing events is
covered by it too, so the probe a household's "enable" runs needs no scope beyond the sync's own."""

SCOPES: tuple[str, ...] = (CALENDAR_EVENTS_SCOPE,)

JWT_BEARER_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"

ASSERTION_LIFETIME_SECONDS = 3600
"""Google caps a signed assertion at one hour after it was issued."""

EXPIRY_MARGIN_SECONDS = 60
"""A cached token is replaced this long before it expires, so a sync that starts with a token
never has it die halfway through its requests."""

FALLBACK_TOKEN_LIFETIME_SECONDS = 300
"""Used only if a token response does not say how long the token lasts."""

SERVICE_ACCOUNT: Final = "service_account"

logger = get_logger()


class GoogleCredentialsError(Exception):
    """`GOOGLE_CALENDAR_CREDENTIALS` is set but is not a credential this module can use."""


class GoogleAuthError(Exception):
    """Google would not, or could not be asked to, trade the credential for an access token."""


@dataclass(frozen=True)
class GoogleCredentials:
    """A service-account key, reduced to the fields the sign-in uses."""

    client_email: str
    """The service account's own address: who the JWT says is asking, and the address a calendar
    is shared with so this credential can write to it."""

    private_key: str = field(repr=False)
    private_key_id: str | None = None
    token_uri: str = GOOGLE_TOKEN_URI

    @property
    def fingerprint(self) -> str:
        """Tells one credential from another in the token cache without holding it as a key."""

        parts = (self.token_uri, self.client_email, self.private_key_id, self.private_key)
        return hashlib.sha256("\x1f".join(part or "" for part in parts).encode()).hexdigest()


def parse_credentials(raw: str) -> GoogleCredentials:
    """Read the JSON a deployer put in `GOOGLE_CALENDAR_CREDENTIALS`.

    Every refusal names what is wrong by field name and never by value.
    """

    try:
        info = json.loads(raw)
    except ValueError:
        raise GoogleCredentialsError("it is not valid JSON") from None

    if not isinstance(info, dict):
        raise GoogleCredentialsError("it must be a JSON object")

    def required(*names: str) -> None:
        missing = [name for name in names if not isinstance(info.get(name), str) or not info[name].strip()]
        if missing:
            raise GoogleCredentialsError(f"missing {', '.join(missing)}")

    if info.get("type") != SERVICE_ACCOUNT:
        raise GoogleCredentialsError(f'"type" must be "{SERVICE_ACCOUNT}"')

    required("client_email", "private_key", "token_uri")
    private_key: str = info["private_key"]
    if "\n" not in private_key.strip():
        # a key that went through one escaping too many arrives with the two characters `\n`
        # where its line breaks were; a PEM reader needs the line breaks
        private_key = private_key.replace("\\n", "\n")

    key_id = info.get("private_key_id")
    return GoogleCredentials(
        token_uri=info["token_uri"].strip(),
        client_email=info["client_email"].strip(),
        private_key_id=key_id if isinstance(key_id, str) and key_id else None,
        private_key=private_key,
    )


@lru_cache(maxsize=2)
def _parse_setting(raw: str) -> GoogleCredentials | None:
    # cached so that a malformed setting is reported once, not on every page load and plan change
    try:
        return parse_credentials(raw)
    except GoogleCredentialsError as e:
        logger.warning("GOOGLE_CALENDAR_CREDENTIALS is set but cannot be used: %s. Calendar sync is off.", e)
        return None


def load_credentials() -> GoogleCredentials | None:
    """The server's Google credential, or None when calendar sync is not set up.

    A setting that is present but unusable also answers None, after one warning in the log that
    says which field is at fault: a household should see "not set up" rather than a sync that
    fails every few minutes.
    """

    raw = get_app_settings().GOOGLE_CALENDAR_CREDENTIALS
    if not raw or not raw.strip():
        return None

    return _parse_setting(raw)


@dataclass(frozen=True)
class AccessToken:
    value: str = field(repr=False)
    expires_at: float


def _assertion(credentials: GoogleCredentials, now: float) -> str:
    """The JWT a service account signs to ask for a token: who it is, what it wants, until when.

    There is no `sub`: that claim is for impersonating a Workspace user, and this credential acts
    only as itself, on calendars that were shared with it.
    """

    issued = int(now)
    claims = {
        "iss": credentials.client_email,
        "scope": " ".join(SCOPES),
        "aud": credentials.token_uri,
        "iat": issued,
        "exp": issued + ASSERTION_LIFETIME_SECONDS,
    }
    headers = {"kid": credentials.private_key_id} if credentials.private_key_id else None

    try:
        return jwt.encode(claims, credentials.private_key, algorithm="RS256", headers=headers)
    except Exception:
        # `from None`, and no quoting: what a key parser says about a key it cannot read is said
        # about the key's own bytes, and this message ends up in a database row and on a page
        raise GoogleAuthError(
            "the private key in GOOGLE_CALENDAR_CREDENTIALS could not sign a request; check it was pasted whole"
        ) from None


def _refusal(response: httpx.Response) -> str:
    """Google's own words for why it refused, e.g. `invalid_grant: Invalid JWT Signature.`"""

    try:
        body = response.json()
    except ValueError:
        return ""

    if not isinstance(body, dict):
        return ""

    words = [str(body[key]) for key in ("error", "error_description") if isinstance(body.get(key), str)]
    return ": ".join(words)


def mint_access_token(credentials: GoogleCredentials, http: httpx.Client, now: float) -> AccessToken:
    """Trade the credential for an access token. One POST, to the credential's own token endpoint."""

    form = {"grant_type": JWT_BEARER_GRANT, "assertion": _assertion(credentials, now)}

    try:
        response = http.post(credentials.token_uri, data=form)
    except (httpx.HTTPError, httpx.InvalidURL) as e:
        raise GoogleAuthError(f"could not reach Google to sign in ({type(e).__name__})") from e

    if response.status_code != 200:
        refusal = _refusal(response)
        raise GoogleAuthError(
            f"Google refused this server's credential ({response.status_code}{': ' + refusal if refusal else ''})"
        )

    try:
        payload = response.json()
        token = payload["access_token"]
        lifetime = float(payload.get("expires_in") or FALLBACK_TOKEN_LIFETIME_SECONDS)
    except (ValueError, KeyError, TypeError):
        raise GoogleAuthError("Google's sign-in answer did not contain an access token") from None

    if not isinstance(token, str) or not token:
        raise GoogleAuthError("Google's sign-in answer did not contain an access token")

    return AccessToken(value=token, expires_at=now + lifetime)


class AccessTokenCache:
    """Keeps each credential's access token in memory until shortly before it expires.

    In memory only: a token is good for an hour and a restart costs one extra sign-in. The lock is
    held across the sign-in itself, so a burst of syncs starting together asks Google once.
    """

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._tokens: dict[str, AccessToken] = {}

    def get(self, credentials: GoogleCredentials, http: httpx.Client) -> str:
        key = credentials.fingerprint
        with self._lock:
            now = self._clock()
            cached = self._tokens.get(key)
            if cached is not None and cached.expires_at - EXPIRY_MARGIN_SECONDS > now:
                return cached.value

            fresh = mint_access_token(credentials, http, now)
            self._tokens[key] = fresh
            return fresh.value

    def forget(self, credentials: GoogleCredentials) -> None:
        """Drop a token Google has just refused, so the next request signs in again."""

        with self._lock:
            self._tokens.pop(credentials.fingerprint, None)

    def clear(self) -> None:
        with self._lock:
            self._tokens.clear()


token_cache = AccessTokenCache()
"""The process-wide cache every calendar client shares."""
