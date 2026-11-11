"""Unit tests for the server's Google credential and the access tokens minted from it.

Both credential shapes are exercised against a fake token endpoint, with a throwaway RSA key made
for the test run. What is checked is what Google checks: the claims and signature of the JWT a
service account sends, and the form an authorized user sends. Nothing here reaches Google.
"""

import json
from datetime import UTC, datetime

import httpx
import jwt
import pytest

from mealie.core.config import get_app_settings
from mealie.services.google_calendar.client import GoogleCalendarClient, GoogleCalendarError
from mealie.services.google_calendar.credentials import (
    CALENDAR_EVENTS_SCOPE,
    EXPIRY_MARGIN_SECONDS,
    GOOGLE_TOKEN_URI,
    AccessTokenCache,
    GoogleAuthError,
    GoogleCredentialsError,
    load_credentials,
    mint_access_token,
    parse_credentials,
)
from tests.utils.fake_google_calendar import (
    SERVICE_ACCOUNT_EMAIL,
    FakeGoogleCalendar,
    authorized_user_info,
    private_key_pem,
    public_key_pem,
    service_account_credentials,
    service_account_info,
)

NOW = 1_800_000_000.0
CALENDAR = "dinner#plan@group.calendar.google.com"


def key_body() -> str:
    """A stretch from the middle of the private key: what must never show up anywhere."""

    return private_key_pem().splitlines()[3]


# ---------------------------------------------------------------------------
# reading the setting


def test_service_account_key_is_read():
    credentials = parse_credentials(json.dumps(service_account_info()))

    assert credentials.kind == "service_account"
    assert credentials.client_email == SERVICE_ACCOUNT_EMAIL
    assert credentials.account_email == SERVICE_ACCOUNT_EMAIL
    assert credentials.token_uri == GOOGLE_TOKEN_URI
    assert credentials.private_key == private_key_pem()
    assert credentials.private_key_id == "test-key-1"


def test_authorized_user_is_read_and_has_no_address_to_share_with():
    credentials = parse_credentials(json.dumps(authorized_user_info()))

    assert credentials.kind == "authorized_user"
    assert credentials.refresh_token == "1//test-refresh-token"
    # the file gcloud writes carries no token endpoint; Google has only the one
    assert credentials.token_uri == GOOGLE_TOKEN_URI
    assert credentials.account_email is None


@pytest.mark.parametrize(
    ("change", "says"),
    [
        ({"private_key": None}, "missing private_key"),
        ({"client_email": "  "}, "missing client_email"),
        ({"token_uri": None, "client_email": None}, "missing client_email, token_uri"),
        ({"type": "api_key"}, '"type" must be'),
        ({"type": None}, '"type" must be'),
    ],
)
def test_an_unusable_key_is_refused_by_field_name_never_by_value(change: dict, says: str):
    info = {k: v for k, v in (service_account_info() | change).items() if v is not None}

    with pytest.raises(GoogleCredentialsError) as refusal:
        parse_credentials(json.dumps(info))

    assert says in str(refusal.value)
    assert key_body() not in str(refusal.value)


def test_authorized_user_without_a_refresh_token_is_refused():
    info = authorized_user_info()
    del info["refresh_token"]

    with pytest.raises(GoogleCredentialsError, match="missing refresh_token"):
        parse_credentials(json.dumps(info))


@pytest.mark.parametrize("raw", ["not json at all", '"a string"', "[1, 2]"])
def test_a_setting_that_is_not_a_json_object_is_refused(raw: str):
    with pytest.raises(GoogleCredentialsError) as refusal:
        parse_credentials(raw)

    assert raw not in str(refusal.value)


def test_a_key_whose_line_breaks_were_escaped_once_too_often_still_signs():
    info = service_account_info()
    info["private_key"] = info["private_key"].replace("\n", "\\n")

    credentials = parse_credentials(json.dumps(info))

    assert credentials.private_key == private_key_pem()


def test_secrets_stay_out_of_repr():
    for credentials in (service_account_credentials(), parse_credentials(json.dumps(authorized_user_info()))):
        shown = repr(credentials)
        assert key_body() not in shown
        assert "test-client-secret" not in shown
        assert "test-refresh-token" not in shown


# ---------------------------------------------------------------------------
# the setting itself


@pytest.fixture
def setting(monkeypatch: pytest.MonkeyPatch):
    """Sets GOOGLE_CALENDAR_CREDENTIALS for one test, and leaves no trace of it in the cached settings."""

    def put(value: str | None) -> None:
        if value is None:
            monkeypatch.delenv("GOOGLE_CALENDAR_CREDENTIALS", raising=False)
        else:
            monkeypatch.setenv("GOOGLE_CALENDAR_CREDENTIALS", value)
        get_app_settings.cache_clear()

    yield put

    monkeypatch.undo()
    get_app_settings.cache_clear()


def test_the_credential_is_read_from_the_setting_and_masked_wherever_settings_are_dumped(setting):
    setting(json.dumps(service_account_info()))

    credentials = load_credentials()
    assert credentials is not None
    assert credentials.client_email == SERVICE_ACCOUNT_EMAIL

    # the app logs its settings at startup; the credential must be stars there, like the other secrets
    settings = get_app_settings()
    assert settings.model_dump()["GOOGLE_CALENDAR_CREDENTIALS"] == "*****"
    assert json.loads(settings.model_dump_json())["GOOGLE_CALENDAR_CREDENTIALS"] == "*****"
    assert key_body() not in settings.model_dump_json()


@pytest.mark.parametrize("value", [None, "", "   "])
def test_no_setting_means_no_credentials(setting, value: str | None):
    setting(value)

    assert load_credentials() is None


def test_a_setting_that_cannot_be_used_means_no_credentials_rather_than_an_error(setting):
    setting('{"type": "service_account", "client_email": "half-a-key@example.test"}')

    assert load_credentials() is None
    assert load_credentials() is None


# ---------------------------------------------------------------------------
# minting a token


def test_service_account_signs_a_jwt_and_trades_it_for_a_token():
    google = FakeGoogleCalendar()

    with google.client() as http:
        token = mint_access_token(service_account_credentials(), http, NOW)

    assert token.value == "ya29.fake-1"
    assert token.expires_at == NOW + 3600

    [form] = google.token_requests
    assert set(form) == {"grant_type", "assertion"}
    assert form["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"

    header = jwt.get_unverified_header(form["assertion"])
    assert header["alg"] == "RS256"
    assert header["typ"] == "JWT"
    assert header["kid"] == "test-key-1"

    # verified with the public half of the key, so this is also the proof the JWT was really signed
    # with the credential's private key; the times are checked by hand because NOW is not now
    claims = jwt.decode(
        form["assertion"],
        public_key_pem(),
        algorithms=["RS256"],
        audience=GOOGLE_TOKEN_URI,
        options={"verify_exp": False, "verify_iat": False},
    )
    assert claims == {
        "iss": SERVICE_ACCOUNT_EMAIL,
        "scope": CALENDAR_EVENTS_SCOPE,
        "aud": GOOGLE_TOKEN_URI,
        "iat": int(NOW),
        "exp": int(NOW) + 3600,
    }
    assert "sub" not in claims


def test_authorized_user_trades_its_refresh_token():
    google = FakeGoogleCalendar()
    credentials = parse_credentials(json.dumps(authorized_user_info()))

    with google.client() as http:
        token = mint_access_token(credentials, http, NOW)

    assert token.value == "ya29.fake-1"
    assert google.token_requests == [
        {
            "grant_type": "refresh_token",
            "client_id": "test-client.apps.googleusercontent.com",
            "client_secret": "test-client-secret",
            "refresh_token": "1//test-refresh-token",
        }
    ]


def test_a_refused_credential_says_what_google_said_and_nothing_secret():
    google = FakeGoogleCalendar()
    google.fail_sign_in(400, "invalid_grant", "Invalid JWT Signature.")

    with google.client() as http, pytest.raises(GoogleAuthError) as refusal:
        mint_access_token(service_account_credentials(), http, NOW)

    assert "invalid_grant: Invalid JWT Signature." in str(refusal.value)
    assert "400" in str(refusal.value)
    assert key_body() not in str(refusal.value)


def test_a_private_key_that_cannot_sign_is_reported_without_quoting_it():
    # the throwaway key cut off after a few lines: PEM-shaped, and not a key any more
    lines = private_key_pem().splitlines()
    info = service_account_info()
    info["private_key"] = "\n".join([*lines[:5], lines[-1]]) + "\n"
    google = FakeGoogleCalendar()

    with google.client() as http, pytest.raises(GoogleAuthError) as refusal:
        mint_access_token(parse_credentials(json.dumps(info)), http, NOW)

    assert "could not sign" in str(refusal.value)
    for line in lines[1:5]:
        assert line not in str(refusal.value)
    # nothing chained either: a traceback must not carry what a key parser said about the key
    assert refusal.value.__cause__ is None
    assert refusal.value.__suppress_context__ is True
    assert google.token_requests == []


def test_an_unreachable_token_endpoint_is_an_auth_error():
    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    with httpx.Client(transport=httpx.MockTransport(offline)) as http, pytest.raises(GoogleAuthError) as refusal:
        mint_access_token(service_account_credentials(), http, NOW)

    assert "could not reach Google" in str(refusal.value)
    assert "ConnectError" in str(refusal.value)


# ---------------------------------------------------------------------------
# keeping a token


def test_a_token_is_kept_until_shortly_before_it_expires_and_then_minted_again():
    google = FakeGoogleCalendar()
    clock = [NOW]
    cache = AccessTokenCache(clock=lambda: clock[0])
    credentials = service_account_credentials()

    with google.client() as http:
        first = cache.get(credentials, http)
        assert cache.get(credentials, http) == first
        assert len(google.token_requests) == 1

        # one second before the safety margin starts: still the same token
        clock[0] = NOW + 3600 - EXPIRY_MARGIN_SECONDS - 1
        assert cache.get(credentials, http) == first
        assert len(google.token_requests) == 1

        # inside the margin: a token that could die mid-sync is replaced before it is used
        clock[0] = NOW + 3600 - EXPIRY_MARGIN_SECONDS + 1
        second = cache.get(credentials, http)
        assert second != first
        assert len(google.token_requests) == 2

        # and the new one is kept in its turn
        assert cache.get(credentials, http) == second
        assert len(google.token_requests) == 2

    # the second assertion was issued at the later time, not replayed
    issued = [
        jwt.decode(form["assertion"], options={"verify_signature": False})["iat"] for form in google.token_requests
    ]
    assert issued == [int(NOW), int(NOW) + 3600 - EXPIRY_MARGIN_SECONDS + 1]


def test_each_credential_keeps_its_own_token():
    google = FakeGoogleCalendar()
    cache = AccessTokenCache(clock=lambda: NOW)
    account = service_account_credentials()
    person = parse_credentials(json.dumps(authorized_user_info()))

    with google.client() as http:
        assert cache.get(account, http) != cache.get(person, http)
        assert cache.get(account, http) == "ya29.fake-1"
        assert cache.get(person, http) == "ya29.fake-2"

    assert len(google.token_requests) == 2


def test_a_forgotten_token_is_minted_again():
    google = FakeGoogleCalendar()
    cache = AccessTokenCache(clock=lambda: NOW)
    credentials = service_account_credentials()

    with google.client() as http:
        first = cache.get(credentials, http)
        cache.forget(credentials)
        assert cache.get(credentials, http) != first


# ---------------------------------------------------------------------------
# using a token


def calendar_client(google: FakeGoogleCalendar, http: httpx.Client) -> GoogleCalendarClient:
    return GoogleCalendarClient(service_account_credentials(), http, AccessTokenCache(clock=lambda: NOW))


def test_calendar_calls_carry_the_token_and_keep_the_calendar_id_in_one_path_segment():
    google = FakeGoogleCalendar()
    google.add_calendar(CALENDAR)

    with google.client() as http:
        client = calendar_client(google, http)
        assert client.access_role(CALENDAR) == "writer"
        created = client.insert_event(CALENDAR, {"start": {"date": "2026-11-12"}, "end": {"date": "2026-11-13"}})
        client.delete_event(CALENDAR, created["id"])

    # the fake splits the raw path on "/" before decoding, so a `#` or `@` sent unencoded would
    # have named a different calendar
    assert [(method, path) for method, path, _ in google.calls] == [
        ("GET", f"{CALENDAR}/events"),
        ("POST", f"{CALENDAR}/events"),
        ("DELETE", f"{CALENDAR}/events/{created['id']}"),
    ]
    assert len(google.token_requests) == 1


def test_a_token_google_no_longer_accepts_is_replaced_once():
    google = FakeGoogleCalendar()
    google.add_calendar(CALENDAR)

    with google.client() as http:
        client = calendar_client(google, http)
        client.access_role(CALENDAR)
        google.revoke_tokens()

        assert client.access_role(CALENDAR) == "writer"

    assert len(google.token_requests) == 2


def test_listing_follows_every_page():
    google = FakeGoogleCalendar()
    google.add_calendar(CALENDAR)
    google.page_size = 2
    for day in range(10, 15):
        google.seed_event(
            CALENDAR,
            {
                "start": {"date": f"2026-11-{day}"},
                "end": {"date": f"2026-11-{day + 1}"},
                "extendedProperties": {"private": {"mealieHousehold": "h-1", "mealieEntry": str(day)}},
            },
        )
    google.seed_event(CALENDAR, {"start": {"date": "2026-11-12"}, "end": {"date": "2026-11-13"}, "summary": "Dentist"})

    with google.client() as http:
        events = calendar_client(google, http).list_events(
            CALENDAR, household_id="h-1", ends_after=datetime(2026, 11, 1, tzinfo=UTC)
        )

    assert sorted(event["start"]["date"] for event in events) == [f"2026-11-{day}" for day in range(10, 15)]
    assert [query.get("pageToken") for query in google.lists()] == [None, "2", "4"]
    assert {query["privateExtendedProperty"] for query in google.lists()} == {"mealieHousehold=h-1"}
    assert {query["timeMin"] for query in google.lists()} == {"2026-11-01T00:00:00Z"}


def test_deleting_an_event_that_is_already_gone_is_not_an_error():
    google = FakeGoogleCalendar()
    google.add_calendar(CALENDAR)

    with google.client() as http:
        client = calendar_client(google, http)
        created = client.insert_event(CALENDAR, {"start": {"date": "2026-11-12"}, "end": {"date": "2026-11-13"}})
        client.delete_event(CALENDAR, created["id"])
        client.delete_event(CALENDAR, created["id"])  # 410
        client.delete_event(CALENDAR, "never-existed")  # 404


def test_a_refused_call_carries_the_status_and_googles_sentence():
    google = FakeGoogleCalendar()

    with google.client() as http, pytest.raises(GoogleCalendarError) as refusal:
        calendar_client(google, http).access_role("nobody-shared-this@group.calendar.google.com")

    assert refusal.value.status_code == 404
    assert str(refusal.value) == "Google Calendar answered 404: Not Found"


def test_an_unreachable_calendar_api_is_a_calendar_error():
    google = FakeGoogleCalendar()
    google.add_calendar(CALENDAR)
    google.fail_calendar_calls(httpx.ConnectError("no route to host"))

    with google.client() as http, pytest.raises(GoogleCalendarError) as refusal:
        calendar_client(google, http).access_role(CALENDAR)

    assert "could not reach Google Calendar (ConnectError)" in str(refusal.value)
