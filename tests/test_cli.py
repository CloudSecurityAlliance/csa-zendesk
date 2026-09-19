"""Tests for the `csa-zendesk` console script.

Every subcommand is exercised by calling `cli.main([...])` directly with an
explicit argv list - never by spawning a subprocess - and every auth-layer
call (`auth.login`, `auth.whoami`, `auth.read`, `auth.token_path`) is
monkeypatched at the `csa_zendesk.auth` module level. `cli.py` does
`from . import auth` and calls `auth.whoami(...)` etc. through attribute
lookup, so patching the attribute on the `auth` module is what a real call
would see - patching the name inside `cli`'s own namespace would not be,
since `cli` never imports the function directly.

No test here makes a network request or touches a real token file: that is
what `tests/auth/*` already covers for the functions this module calls.
"""

from __future__ import annotations

import pytest

from csa_zendesk import auth, cli


def _tokens(*, expires_at: float = 0.0, scope: str = "read") -> auth.Tokens:
    return auth.Tokens(access_token="AT-SECRET", refresh_token="RT-SECRET", expires_at=expires_at, scope=scope)


# ---------------------------------------------------------------------------
# _human_duration / _human_expiry - pure, deterministic, every branch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "want"),
    [
        (0, "0 seconds"),
        (1, "1 second"),
        (59, "59 seconds"),
        # just over the 60-second boundary: the next unit down (seconds) is
        # non-zero, so it is carried rather than truncated away.
        (60, "1 minute"),
        (61, "1 minute 1 second"),
        (119, "1 minute 59 seconds"),
        (120, "2 minutes"),
        (125, "2 minutes 5 seconds"),
        # just under the 60-minute boundary: previously truncated to "59
        # minutes", which is the exact bug this fix closes.
        (3599, "59 minutes 59 seconds"),
        (3600, "1 hour"),
        # just over 1 hour: minutes is the next unit down, and it IS zero here
        # even though seconds is not - only one unit below the primary is ever
        # shown, so the leftover second is dropped, not carried past minutes.
        (3601, "1 hour"),
        (3660, "1 hour 1 minute"),
        (7200, "2 hours"),
        # just under the 24-hour boundary: previously truncated to "23 hours".
        (86399, "23 hours 59 minutes"),
        (86400, "1 day"),
        (90000, "1 day 1 hour"),
        (172800, "2 days"),
        # the case that exposed the bug: a freshly-issued 2-day access token
        # (172,800s max) measured a few seconds later reads as "1 day" under
        # the old truncating implementation, as if half its life were already
        # gone.
        (172753, "1 day 23 hours"),
    ],
)
def test_human_duration_every_scale(seconds, want):
    assert cli._human_duration(seconds) == want


def test_human_expiry_in_the_future():
    assert cli._human_expiry(1_400.0, now=0.0) == "expires in 23 minutes 20 seconds"


def test_human_expiry_in_the_past():
    assert cli._human_expiry(0.0, now=600.0) == "expired 10 minutes ago"


def test_human_expiry_exactly_now_reads_as_expired():
    # remaining == 0 takes the "expired" branch, not a divide-by-zero or a
    # "expires in 0 seconds" that would be true for an instant and false a
    # moment later.
    assert cli._human_expiry(100.0, now=100.0) == "expired 0 seconds ago"


# ---------------------------------------------------------------------------
# auth login
# ---------------------------------------------------------------------------


def test_login_reports_identity_and_scope_to_stderr_never_stdout(monkeypatch, capsys):
    calls = {}

    def fake_login(*, scopes, open_browser, paste, **_):
        calls["scopes"] = scopes
        calls["open_browser"] = open_browser
        calls["paste"] = paste
        return _tokens(scope="read tickets:write")

    monkeypatch.setattr(auth, "login", fake_login)
    monkeypatch.setattr(auth, "whoami", lambda: {"name": "Agent Smith", "email": "agent@example.com"})

    rc = cli.main(["auth", "login"])
    out, err = capsys.readouterr()

    assert rc == 0
    assert out == ""
    assert "Agent Smith" in err
    assert "read tickets:write" in err
    assert "AT-SECRET" not in err and "RT-SECRET" not in err
    assert calls == {"scopes": ("read",), "open_browser": True, "paste": False}


def test_login_reads_scopes_from_the_environment(monkeypatch, capsys):
    monkeypatch.setenv("CSA_ZENDESK_SCOPES", "read tickets:write ticket_views:write")
    calls = {}

    def fake_login(*, scopes, **_):
        calls["scopes"] = scopes
        return _tokens()

    monkeypatch.setattr(auth, "login", fake_login)
    monkeypatch.setattr(auth, "whoami", lambda: {"name": "Agent"})

    cli.main(["auth", "login"])

    assert calls["scopes"] == ("read", "tickets:write", "ticket_views:write")


def test_login_forwards_paste_and_no_browser_flags(monkeypatch, capsys):
    calls = {}

    def fake_login(*, open_browser, paste, **_):
        calls["open_browser"] = open_browser
        calls["paste"] = paste
        return _tokens()

    monkeypatch.setattr(auth, "login", fake_login)
    monkeypatch.setattr(auth, "whoami", lambda: {"name": "Agent"})

    cli.main(["auth", "login", "--paste", "--no-browser"])

    assert calls == {"open_browser": False, "paste": True}


def test_login_falls_back_to_email_when_name_is_missing(monkeypatch, capsys):
    monkeypatch.setattr(auth, "login", lambda **_: _tokens())
    monkeypatch.setattr(auth, "whoami", lambda: {"email": "agent@example.com"})

    cli.main(["auth", "login"])
    _, err = capsys.readouterr()

    assert "agent@example.com" in err


def test_login_falls_back_to_a_generic_label_when_identity_has_neither(monkeypatch, capsys):
    monkeypatch.setattr(auth, "login", lambda **_: _tokens())
    monkeypatch.setattr(auth, "whoami", lambda: {"id": 1})

    cli.main(["auth", "login"])
    _, err = capsys.readouterr()

    assert "(unnamed account)" in err


def test_login_succeeds_but_the_identity_check_afterwards_fails(monkeypatch, capsys):
    monkeypatch.setattr(auth, "login", lambda **_: _tokens(scope="read"))
    monkeypatch.setattr(auth, "whoami", lambda: (_ for _ in ()).throw(auth.NotAuthenticated("anonymous")))

    rc = cli.main(["auth", "login"])
    out, err = capsys.readouterr()

    assert rc == 1
    assert out == ""
    assert "anonymous" in err
    assert "read" in err  # the scope that WAS granted is still reported


def test_login_failure_is_a_message_not_a_traceback(monkeypatch, capsys):
    def fake_login(**_):
        raise auth.NotAuthorised("CSA_ZENDESK_SUBDOMAIN is not set.")

    monkeypatch.setattr(auth, "login", fake_login)

    rc = cli.main(["auth", "login"])
    out, err = capsys.readouterr()

    assert rc == 1
    assert out == ""
    assert "CSA_ZENDESK_SUBDOMAIN is not set." in err


# ---------------------------------------------------------------------------
# auth whoami
# ---------------------------------------------------------------------------


def test_whoami_prints_the_selected_fields_to_stdout(monkeypatch, capsys):
    monkeypatch.setattr(
        auth,
        "whoami",
        lambda: {"id": 42, "name": "Agent Smith", "email": "agent@example.com", "role": "admin", "phone": "555"},
    )

    rc = cli.main(["auth", "whoami"])
    out, err = capsys.readouterr()

    assert rc == 0
    assert err == ""
    assert "id: 42" in out
    assert "name: Agent Smith" in out
    assert "email: agent@example.com" in out
    assert "role: admin" in out
    assert "phone" not in out  # not in the printed allowlist


def test_whoami_omits_a_missing_field_rather_than_printing_it_blank(monkeypatch, capsys):
    monkeypatch.setattr(auth, "whoami", lambda: {"id": 1, "name": "Agent"})

    cli.main(["auth", "whoami"])
    out, _ = capsys.readouterr()

    assert "email:" not in out
    assert "role:" not in out


def test_whoami_failure_is_a_message_not_a_traceback(monkeypatch, capsys):
    monkeypatch.setattr(auth, "whoami", lambda: (_ for _ in ()).throw(auth.NotAuthenticated("looks anonymous")))

    rc = cli.main(["auth", "whoami"])
    out, err = capsys.readouterr()

    assert rc == 1
    assert out == ""
    assert "looks anonymous" in err


# ---------------------------------------------------------------------------
# auth status
# ---------------------------------------------------------------------------


def test_status_reports_no_token_file(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(auth, "token_path", lambda: tmp_path / "tokens.json")
    monkeypatch.setattr(auth, "read", lambda: None)

    rc = cli.main(["auth", "status"])
    out, err = capsys.readouterr()

    assert rc == 1
    assert out == ""
    assert "no token file" in err
    assert "csa-zendesk auth login" in err


def test_status_reports_path_expiry_and_scope_never_the_token(monkeypatch, capsys, tmp_path):
    path = tmp_path / "tokens.json"
    monkeypatch.setattr(auth, "token_path", lambda: path)
    monkeypatch.setattr(auth, "read", lambda: _tokens(expires_at=1_400.0, scope="read tickets:write"))
    monkeypatch.setattr(cli.time, "time", lambda: 0.0)

    rc = cli.main(["auth", "status"])
    out, err = capsys.readouterr()

    assert rc == 0
    assert err == ""
    assert str(path) in out
    assert "expires in 23 minutes" in out
    assert "scope: read tickets:write" in out
    assert "AT-SECRET" not in out and "RT-SECRET" not in out


def test_status_shows_an_expired_token_as_expired(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(auth, "token_path", lambda: tmp_path / "tokens.json")
    monkeypatch.setattr(auth, "read", lambda: _tokens(expires_at=0.0))
    monkeypatch.setattr(cli.time, "time", lambda: 600.0)

    cli.main(["auth", "status"])
    out, _ = capsys.readouterr()

    assert "expired 10 minutes ago" in out


def test_status_failure_is_a_message_not_a_traceback(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(auth, "token_path", lambda: tmp_path / "tokens.json")

    def fake_read():
        raise auth.TokenFileError("has mode 0644, expected 0600")

    monkeypatch.setattr(auth, "read", fake_read)

    rc = cli.main(["auth", "status"])
    out, err = capsys.readouterr()

    assert rc == 1
    assert out == ""
    assert "0644" in err


# ---------------------------------------------------------------------------
# auth logout
# ---------------------------------------------------------------------------


def test_logout_with_no_token_file_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(auth, "logout", lambda **_: "no-token")

    rc = cli.main(["auth", "logout"])
    out, err = capsys.readouterr()

    assert rc == 0
    assert out == ""
    assert "already logged out" in err


def test_logout_of_an_already_invalid_token_still_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(auth, "logout", lambda **_: "already-invalid")

    rc = cli.main(["auth", "logout"])
    out, err = capsys.readouterr()

    assert rc == 0
    assert out == ""
    assert "already invalid" in err


def test_a_successful_logout_exits_zero_and_says_revoked(monkeypatch, capsys):
    monkeypatch.setattr(auth, "logout", lambda **_: "revoked")

    rc = cli.main(["auth", "logout"])
    out, err = capsys.readouterr()

    assert rc == 0
    assert out == ""
    assert "revoked" in err


def test_a_revoke_error_exits_nonzero_names_the_admin_center_fallback_and_never_a_token(monkeypatch, capsys):
    def fake_logout(**_):
        raise auth.RevokeError("Zendesk refused to revoke the token (HTTP 503).")

    monkeypatch.setattr(auth, "logout", fake_logout)

    rc = cli.main(["auth", "logout"])
    out, err = capsys.readouterr()

    assert rc == 1
    assert out == ""
    assert "503" in err
    assert "may still be live" in err
    assert "Admin Center" in err
    assert "AT-SECRET" not in err and "RT-SECRET" not in err


def test_a_transport_failure_during_logout_also_names_the_admin_center_fallback(monkeypatch, capsys):
    from csa_zendesk import exceptions as exc

    def fake_logout(**_):
        raise exc.ApiError("could not reach the Zendesk OAuth revoke endpoint (ConnectError)")

    monkeypatch.setattr(auth, "logout", fake_logout)

    rc = cli.main(["auth", "logout"])
    out, err = capsys.readouterr()

    assert rc == 1
    assert out == ""
    assert "Admin Center" in err


def test_logout_with_no_subdomain_configured_is_a_message_not_a_traceback(monkeypatch, capsys):
    # NotAuthorised (missing CSA_ZENDESK_SUBDOMAIN) is not a revoke failure -
    # it must fall through to main()'s generic ZendeskError handler, not the
    # logout-specific "may still be live" message, which would be misleading
    # here: no revoke attempt was ever made.
    def fake_logout(**_):
        raise auth.NotAuthorised("CSA_ZENDESK_SUBDOMAIN is not set.")

    monkeypatch.setattr(auth, "logout", fake_logout)

    rc = cli.main(["auth", "logout"])
    out, err = capsys.readouterr()

    assert rc == 1
    assert out == ""
    assert "CSA_ZENDESK_SUBDOMAIN is not set." in err
    assert "Admin Center" not in err
