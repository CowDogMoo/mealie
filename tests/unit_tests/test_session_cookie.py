import re
from datetime import timedelta

from starlette.requests import Request
from starlette.responses import Response

from mealie.core.config import get_app_settings
from mealie.routes.auth.auth import SESSION_COOKIE_NAME, session_cookie_attrs, set_session_cookie


def build_request(scheme: str = "http", headers: dict[str, str] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "scheme": scheme,
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "server": ("testserver", 80),
            "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        }
    )


def test_plain_http_gets_an_insecure_lax_cookie():
    attrs = session_cookie_attrs(build_request())

    assert attrs["secure"] is False
    assert attrs["samesite"] == "lax"
    assert attrs["partitioned"] is False


def test_https_gets_a_secure_cookie():
    attrs = session_cookie_attrs(build_request(scheme="https"))

    assert attrs["secure"] is True


def test_forwarded_proto_is_honoured_when_the_proxy_is_not_trusted():
    """uvicorn only rewrites the scheme for proxies HOST_IP trusts; narrowing it dropped `Secure`."""
    attrs = session_cookie_attrs(build_request(headers={"x-forwarded-proto": "https"}))

    assert attrs["secure"] is True


def test_the_first_hop_of_a_forwarded_chain_decides():
    """Proxies append, so the browser's own protocol is the first entry rather than the last."""
    assert session_cookie_attrs(build_request(headers={"x-forwarded-proto": "https, http"}))["secure"] is True
    assert session_cookie_attrs(build_request(headers={"x-forwarded-proto": "http, https"}))["secure"] is False


def test_embedded_over_https_relaxes_samesite_and_partitions():
    attrs = session_cookie_attrs(build_request(scheme="https", headers={"x-mealie-embedded": "true"}))

    assert attrs["samesite"] == "none"
    assert attrs["partitioned"] is True


def test_embedded_behind_an_untrusted_proxy_still_relaxes_samesite():
    """The regression this guards: degrading to Lax silently breaks cross-site iframe embedding."""
    attrs = session_cookie_attrs(build_request(headers={"x-forwarded-proto": "https", "x-mealie-embedded": "true"}))

    assert attrs["samesite"] == "none"
    assert attrs["partitioned"] is True


def test_embedded_over_plain_http_stays_lax():
    """Browsers reject `SameSite=None` without `Secure`, so it must not be emitted over HTTP."""
    attrs = session_cookie_attrs(build_request(headers={"x-mealie-embedded": "true"}))

    assert attrs["samesite"] == "lax"
    assert attrs["partitioned"] is False


def _set_cookie_header(expires_in: timedelta, remember_me: bool) -> str:
    response = Response()
    set_session_cookie(response, build_request(scheme="https"), "a-token", expires_in, remember_me)
    return response.headers["set-cookie"]


def _max_age(header: str) -> int | None:
    match = re.search(r"Max-Age=(\d+)", header)
    return int(match.group(1)) if match else None


def test_a_remembered_session_cookie_lives_exactly_as_long_as_its_token():
    """The cookie's lifetime comes from the token it carries.

    It used to be written by the client as `TOKEN_TIME` hours, independent of the token's real
    expiry. Any deployment whose token outlived TOKEN_TIME - a remember-me login, for one - had the
    browser discard a still-valid token and drop the user back to an anonymous session.
    """
    assert _max_age(_set_cookie_header(timedelta(days=14), remember_me=True)) == 14 * 24 * 60 * 60


def test_the_cookie_tracks_the_token_even_when_it_disagrees_with_token_time():
    """The distinguishing case: a duration TOKEN_TIME could not have produced."""
    settings = get_app_settings()
    odd = timedelta(hours=settings.TOKEN_TIME + 37, minutes=13)

    assert _max_age(_set_cookie_header(odd, remember_me=True)) == int(odd.total_seconds())
    assert _max_age(_set_cookie_header(odd, remember_me=True)) != settings.TOKEN_TIME * 60 * 60


def test_without_remember_me_the_cookie_dies_with_the_browser_session():
    header = _set_cookie_header(timedelta(days=14), remember_me=False)

    assert _max_age(header) is None
    assert SESSION_COOKIE_NAME in header
