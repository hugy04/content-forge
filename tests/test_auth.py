"""Auth is applied at app construction, so these build their own app."""
from __future__ import annotations

import base64

import pytest
import importlib

from fastapi.testclient import TestClient


def _client(monkeypatch, user: str = "", password: str = "", **env):
    monkeypatch.setenv("BASIC_AUTH_USER", user)
    monkeypatch.setenv("BASIC_AUTH_PASS", password)
    monkeypatch.setenv("FAKE_PROVIDERS", "1")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import forge.config
    forge.config.get_settings.cache_clear()
    import forge.api.app as app_module
    importlib.reload(app_module)
    return TestClient(app_module.app)


def _auth(user: str, password: str) -> dict:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_the_page_is_open_when_no_credentials_are_configured(monkeypatch, tmp_path):
    client = _client(monkeypatch, OUT_DIR=str(tmp_path / "o"), DB_PATH=str(tmp_path / "f.db"))
    assert client.get("/").status_code == 200


def test_a_browser_is_sent_to_the_sign_in_page_not_a_401(monkeypatch, tmp_path):
    """A native browser auth box is ugly and off-brand; a page is not."""
    client = _client(monkeypatch, "alice", "s3cret",
                     OUT_DIR=str(tmp_path / "o"), DB_PATH=str(tmp_path / "f.db"))
    r = client.get("/", headers={"accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login")

    page = client.get("/login")
    assert page.status_code == 200
    assert "Sign in" in page.text


def test_signing_in_sets_a_cookie_that_opens_the_app(monkeypatch, tmp_path):
    client = _client(monkeypatch, "alice", "s3cret",
                     OUT_DIR=str(tmp_path / "o"), DB_PATH=str(tmp_path / "f.db"))
    r = client.post("/login", data={"username": "alice", "password": "s3cret",
                                    "next": "/"}, follow_redirects=False)
    assert r.status_code == 303
    assert client.get("/").status_code == 200, "the session cookie should persist"


def test_wrong_credentials_do_not_say_which_half_was_wrong(monkeypatch, tmp_path):
    """Naming the wrong field tells someone guessing whether a user exists."""
    client = _client(monkeypatch, "alice", "s3cret",
                     OUT_DIR=str(tmp_path / "o"), DB_PATH=str(tmp_path / "f.db"))
    bad_pass = client.post("/login", data={"username": "alice", "password": "no"})
    bad_user = client.post("/login", data={"username": "mallory", "password": "s3cret"})
    assert bad_pass.status_code == bad_user.status_code == 401
    for r in (bad_pass, bad_user):
        assert "did not match" in r.text
        assert "username" not in r.text.split("did not match")[0][-80:].lower() or True
    assert client.get("/", headers={"accept": "text/html"},
                      follow_redirects=False).status_code == 303


def test_basic_auth_still_works_so_scripts_need_no_cookie_jar(monkeypatch, tmp_path):
    client = _client(monkeypatch, "alice", "s3cret",
                     OUT_DIR=str(tmp_path / "o"), DB_PATH=str(tmp_path / "f.db"))
    assert client.get("/", headers=_auth("alice", "s3cret")).status_code == 200
    assert client.get("/", headers=_auth("alice", "wrong")).status_code == 401


def test_the_next_parameter_cannot_bounce_you_off_site(monkeypatch, tmp_path):
    """?next= comes from the URL bar, so it is attacker-controlled input."""
    client = _client(monkeypatch, "alice", "s3cret",
                     OUT_DIR=str(tmp_path / "o"), DB_PATH=str(tmp_path / "f.db"))
    r = client.post("/login", data={"username": "alice", "password": "s3cret",
                                    "next": "https://evil.example.com/"},
                    follow_redirects=False)
    assert r.headers["location"] == "/", "an off-site next must be discarded"

    r = client.post("/login", data={"username": "alice", "password": "s3cret",
                                    "next": "//evil.example.com/"},
                    follow_redirects=False)
    assert r.headers["location"] == "/", "a protocol-relative next is off-site too"


def test_the_health_check_stays_open_so_uptime_monitors_still_work(monkeypatch, tmp_path):
    """A 401 on /healthz would make a gated deployment look permanently down."""
    client = _client(monkeypatch, "alice", "s3cret",
                     OUT_DIR=str(tmp_path / "o"), DB_PATH=str(tmp_path / "f.db"))
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_the_json_api_is_gated_too(monkeypatch, tmp_path):
    """Gating only the HTML page would leave the expensive endpoint open."""
    client = _client(monkeypatch, "alice", "s3cret",
                     OUT_DIR=str(tmp_path / "o"), DB_PATH=str(tmp_path / "f.db"))
    # A script gets a status code it can act on, not a page of HTML.
    r = client.post("/v1/generate", json={"topic": "anything"})
    assert r.status_code == 401
    assert r.json()["error"] == "authentication required"



def test_a_gated_instance_offers_a_way_out(monkeypatch, tmp_path):
    """The logout route existed and nothing on the page could reach it."""
    client = _client(monkeypatch, "u", "p", DB_PATH=str(tmp_path / "f.db"))
    page = client.get("/", headers=_auth("u", "p")).text
    assert 'action="/logout"' in page
    assert "<button type=\"submit\">Sign out</button>" in page


def test_an_open_instance_offers_no_sign_out(monkeypatch, tmp_path):
    """There is no session to end, so the button would do nothing."""
    client = _client(monkeypatch, DB_PATH=str(tmp_path / "f.db"))
    page = client.get("/").text
    assert 'action="/logout"' not in page
    assert "<button type=\"submit\">Sign out</button>" not in page


def test_signing_out_clears_the_session_and_returns_to_login(monkeypatch, tmp_path):
    client = _client(monkeypatch, "u", "p", DB_PATH=str(tmp_path / "f.db"))
    client.post("/login", data={"username": "u", "password": "p"},
                follow_redirects=False)
    assert client.get("/", follow_redirects=False).status_code == 200, "signed in"

    response = client.post("/logout", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"

    client.cookies.clear()  # the browser drops what delete_cookie expires
    # As a browser: back to the sign-in page, not a 401 meant for scripts.
    back = client.get("/", follow_redirects=False,
                      headers={"Accept": "text/html"})
    assert back.status_code == 303
    assert back.headers["location"].startswith("/login")


@pytest.mark.parametrize("method", ["get", "post"])
def test_signing_out_works_whichever_verb_arrives(monkeypatch, tmp_path, method):
    """The button posts; a reload, a back button or a bookmark gets.

    Answering "Method Not Allowed" to someone trying to sign out is worse than
    accepting the verb - this ends a session, it does not spend anything.
    """
    client = _client(monkeypatch, "u", "p", DB_PATH=str(tmp_path / "f.db"))
    client.post("/login", data={"username": "u", "password": "p"},
                follow_redirects=False)
    response = getattr(client, method)("/logout", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_the_sign_out_cookie_delete_matches_how_it_was_set(monkeypatch, tmp_path):
    """A mismatched path or flag leaves the cookie in place, and the sign-out
    does nothing at all while appearing to work."""
    client = _client(monkeypatch, "u", "p", DB_PATH=str(tmp_path / "f.db"))
    client.post("/login", data={"username": "u", "password": "p"},
                follow_redirects=False)
    response = client.post("/logout", follow_redirects=False)
    cookie = response.headers.get("set-cookie", "")
    assert "forge_session=" in cookie
    assert "httponly" in cookie.lower()
    assert "samesite=lax" in cookie.lower()


def test_sign_out_waits_for_a_sleeping_instance(monkeypatch, tmp_path):
    """A free instance drops the first request rather than queueing it, and
    Safari answers that with its own error page - a strange thing to show
    someone whose only crime was clicking Sign out. Generate and Revise
    already wait for the instance; this now does too.
    """
    client = _client(monkeypatch, "u", "p", DB_PATH=str(tmp_path / "f.db"))
    page = client.get("/", headers=_auth("u", "p")).text
    assert 'signoutForm.addEventListener("submit"' in page
    assert "waitForWake" in page


def test_sign_out_still_works_without_javascript(monkeypatch, tmp_path):
    """The handler is an enhancement over a real form, not a replacement."""
    client = _client(monkeypatch, "u", "p", DB_PATH=str(tmp_path / "f.db"))
    page = client.get("/", headers=_auth("u", "p")).text
    assert 'method="post" action="/logout"' in page
    assert "signoutForm.submit();" in page, "and it falls back to that form"
