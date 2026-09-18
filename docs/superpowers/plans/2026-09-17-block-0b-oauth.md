# Block 0b — OAuth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the library and every script in `scripts/` authenticate by OAuth, so that no API token is read anywhere and nothing remains on the credential model ADR-015 deleted.

**Architecture:** A public OAuth client using `authorization_code` with PKCE (S256) and no secret. A token file holds the refresh token, the access token and its expiry — nothing else. `access_token()` is the single accessor: it refreshes before expiry, and callers hand it to `HttpClient` as the `token_provider` callable Block 0 already takes. `scripts/zd.authorize()` becomes the same call.

**Tech Stack:** Python ≥3.10 · `httpx` · stdlib `http.server`, `secrets`, `hashlib`, `base64` · `pytest` · `ruff` · `mypy --strict`

**Spec:** [`DECISIONS-ADR/ADR-009.md`](../../../DECISIONS-ADR/ADR-009.md) (the flow, the store, refresh) and [`DECISIONS-ADR/ADR-015.md`](../../../DECISIONS-ADR/ADR-015.md) (OAuth only, nothing exempt). Auth research in [`analysis/API-SURFACE.md`](../../../analysis/API-SURFACE.md) §7.

## Global Constraints

Block 0's constraints all still apply. Repeating the ones this block can break:

- **Never interpolate a credential** into a message, a log line, or a `__repr__`. This block handles four of them — authorization code, code verifier, access token, refresh token — and all four are in scope.
- **Nothing may write to stdout.** Under stdio MCP, stdout *is* the JSON-RPC channel. The browser-flow prompt is the obvious violation waiting to happen: it goes to **stderr**.
- **`CSA_ZENDESK_SUBDOMAIN` has no default.** Neither does `CSA_ZENDESK_MCP_SERVER_IDENTIFIER` — ADR-009 rejected embedding a CSA client id.
- **100% coverage, enforced.** `--cov-fail-under=100`. A gate below the measured state cannot fail.
- **`mypy --strict` over `src` only.** `ruff` with line length 120.
- **No network in tests, ever.** `httpx.MockTransport` for HTTP; a real loopback socket on port 0 is fine for the callback listener, as it is not the network.
- **Keyword-only arguments** on anything `PolicyBackend` might wrap.
- **Branch and PR**; `scripts/check_public_safe.py` passes before every push.

## Divergence from ADR-009, recorded

ADR-009's Implementation section says *"`auth.py` owns the flow, the store and refresh."* This plan builds a **package**, `src/csa_zendesk/auth/`, with four modules instead.

The ADR was written on 2026-09-08. Block 0 then discovered (TODO **E8**) that `_http.py` was approaching ADR-002's 400-line tripwire with three additions owed, one of which is this block. A single `auth.py` holding PKCE, a socket listener, an HTTP exchange, a file store with locking, and refresh logic starts over that line rather than approaching it. This is an implementation split, not a reversal — every decision in ADR-009 stands.

## File Structure

| File | Responsibility |
|---|---|
| `src/csa_zendesk/auth/__init__.py` | The public surface: `access_token`, `login`, `whoami`, `AuthError` re-exports. Nothing else imports the private modules. |
| `src/csa_zendesk/auth/_pkce.py` | Verifier/challenge generation and the authorization URL. Pure functions, no I/O. |
| `src/csa_zendesk/auth/_store.py` | The token file: path resolution, mode assertion, atomic write, lock. Knows nothing about HTTP. |
| `src/csa_zendesk/auth/_callback.py` | One-shot loopback listener for the redirect, plus the paste fallback. Knows nothing about tokens. |
| `src/csa_zendesk/auth/_flow.py` | Code exchange, scope verification, refresh, and `access_token()`. The only module that talks to Zendesk. |
| `src/csa_zendesk/auth/whoami.py` | Identity check that does not trust `users/me.json`. |
| `tests/auth/test_*.py` | One test module per source module. |

---

### Task 1: The token store — path, permissions, atomic write

**Files:**
- Create: `src/csa_zendesk/auth/__init__.py`, `src/csa_zendesk/auth/_store.py`
- Test: `tests/auth/test_store.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Tokens` (frozen dataclass: `access_token: str`, `refresh_token: str`, `expires_at: float`), `token_path() -> pathlib.Path`, `read() -> Tokens | None`, `write(tokens: Tokens) -> None`, `clear() -> None`, exception `TokenFileError`.

- [ ] **Step 1: Write the failing tests for path resolution**

```python
# tests/auth/test_store.py
import pathlib
import pytest
from csa_zendesk.auth import _store


def test_explicit_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "custom.json"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert _store.token_path() == tmp_path / "custom.json"


def test_xdg_is_used_when_set(monkeypatch, tmp_path):
    monkeypatch.delenv("CSA_ZENDESK_TOKEN_FILE", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert _store.token_path() == tmp_path / "xdg" / "csa-zendesk" / "tokens.json"


def test_home_config_is_the_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("CSA_ZENDESK_TOKEN_FILE", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert _store.token_path() == tmp_path / ".config" / "csa-zendesk" / "tokens.json"
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/auth/test_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'csa_zendesk.auth'`

- [ ] **Step 3: Implement path resolution**

```python
# src/csa_zendesk/auth/_store.py
"""The token file. ADR-009: exactly one persisted artifact, holding a credential
and nothing else.

ADR-005 forbids persisting *response* data. A refresh token is a credential, which
is the category SECURITY.md already protects, not customer data, which is the
category ADR-005 exists to keep off disk. Conflating the two produced a
contradiction across three documents; separating them resolves it.
"""

from __future__ import annotations

import json
import os
import pathlib
import stat
import tempfile
from dataclasses import dataclass

from .. import exceptions as exc


class TokenFileError(exc.ZendeskError):
    """The token file exists but cannot be trusted."""


@dataclass(frozen=True, slots=True)
class Tokens:
    access_token: str
    refresh_token: str
    expires_at: float

    def __repr__(self) -> str:  # never let a credential reach a log line
        return f"Tokens(expires_at={self.expires_at!r}, credentials=<redacted>)"


def token_path() -> pathlib.Path:
    override = os.environ.get("CSA_ZENDESK_TOKEN_FILE")
    if override:
        return pathlib.Path(override)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = pathlib.Path(xdg) if xdg else pathlib.Path.home() / ".config"
    return base / "csa-zendesk" / "tokens.json"
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/auth/test_store.py -v`
Expected: 3 passed

- [ ] **Step 5: Write the failing tests for permissions and content**

```python
def test_write_creates_0600_in_a_0700_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "d" / "tokens.json"))
    _store.write(_store.Tokens("at", "rt", 1000.0))
    p = _store.token_path()
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert stat.S_IMODE(p.parent.stat().st_mode) == 0o700


def test_the_file_holds_exactly_three_fields(monkeypatch, tmp_path):
    # ADR-009: "the refresh token, the access token and its expiry. Nothing else.
    # No ticket data." This test is the enforcement of that sentence.
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "tokens.json"))
    _store.write(_store.Tokens("at", "rt", 1000.0))
    raw = json.loads(_store.token_path().read_text())
    assert set(raw) == {"access_token", "refresh_token", "expires_at"}


def test_a_world_readable_file_is_a_loud_error_not_a_warning(monkeypatch, tmp_path):
    # ADR-009: "a 0644 token file is a finding, not a preference."
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "tokens.json"))
    _store.write(_store.Tokens("at", "rt", 1000.0))
    _store.token_path().chmod(0o644)
    with pytest.raises(_store.TokenFileError, match="0644"):
        _store.read()


def test_read_returns_none_when_there_is_no_file(monkeypatch, tmp_path):
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "absent.json"))
    assert _store.read() is None


def test_a_credential_never_appears_in_a_repr(monkeypatch, tmp_path):
    t = _store.Tokens("SECRET-ACCESS", "SECRET-REFRESH", 1.0)
    assert "SECRET-ACCESS" not in repr(t)
    assert "SECRET-REFRESH" not in repr(t)
```

- [ ] **Step 6: Run to verify they fail**

Run: `python -m pytest tests/auth/test_store.py -v`
Expected: FAIL — `AttributeError: module has no attribute 'write'`

- [ ] **Step 7: Implement read/write/clear**

```python
def _ensure_dir(path: pathlib.Path) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)


def write(tokens: Tokens) -> None:
    """Atomically replace the token file.

    Temp file then rename, because refresh rotates the token and two MCP clients
    may run this server concurrently (ADR-009). A partial write would otherwise
    leave a file that parses as valid JSON and authenticates as nothing.

    The mode is set on the temp file *before* the rename, so the credential is
    never briefly world-readable under its final name.
    """
    path = token_path()
    _ensure_dir(path)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tokens-", suffix=".json")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(
                {
                    "access_token": tokens.access_token,
                    "refresh_token": tokens.refresh_token,
                    "expires_at": tokens.expires_at,
                },
                fh,
            )
        os.replace(tmp, path)
    except BaseException:
        pathlib.Path(tmp).unlink(missing_ok=True)
        raise


def read() -> Tokens | None:
    """Load the tokens, asserting the file's mode on every read.

    Checked every time rather than at login, because the dangerous case is a file
    that was correct when written and was later chmodded, copied, or restored from
    a backup that did not preserve modes.
    """
    path = token_path()
    if not path.exists():
        return None
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode != 0o600:
        raise TokenFileError(
            f"{path} has mode {mode:04o}, expected 0600. A token file readable by "
            f"anyone else is a finding, not a preference. Fix it with "
            f"`chmod 600 {path}` and consider the credential compromised."
        )
    try:
        raw = json.loads(path.read_text())
        return Tokens(
            access_token=raw["access_token"],
            refresh_token=raw["refresh_token"],
            expires_at=float(raw["expires_at"]),
        )
    except (ValueError, KeyError, TypeError) as e:
        raise TokenFileError(
            f"{path} is not a readable token file ({type(e).__name__}). Delete it and "
            f"run `csa-zendesk auth login` again."
        ) from e


def clear() -> None:
    token_path().unlink(missing_ok=True)
```

- [ ] **Step 8: Run to verify they pass**

Run: `python -m pytest tests/auth/test_store.py -v`
Expected: 8 passed

- [ ] **Step 9: Commit**

```bash
git add src/csa_zendesk/auth/ tests/auth/
git commit -m "feat(auth): the token file, with its mode asserted on every read"
```

---

### Task 2: PKCE and the authorization URL

**Files:**
- Create: `src/csa_zendesk/auth/_pkce.py`
- Test: `tests/auth/test_pkce.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `new_verifier() -> str`, `challenge_for(verifier: str) -> str`, `authorize_url(*, subdomain: str, client_id: str, redirect_uri: str, scopes: Sequence[str], challenge: str, state: str) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/auth/test_pkce.py
import base64
import hashlib
import re
from urllib.parse import parse_qs, urlparse

from csa_zendesk.auth import _pkce


def test_the_verifier_is_long_enough_and_url_safe():
    # RFC 7636 §4.1: 43-128 characters from the unreserved set.
    v = _pkce.new_verifier()
    assert 43 <= len(v) <= 128
    assert re.fullmatch(r"[A-Za-z0-9\-._~]+", v)


def test_two_verifiers_are_never_the_same():
    assert _pkce.new_verifier() != _pkce.new_verifier()


def test_the_challenge_is_s256_not_plain():
    # ADR-009: PKCE (S256) is mandatory. A "plain" challenge would equal the
    # verifier, which is the degenerate case this asserts against.
    v = "a" * 43
    expected = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    assert _pkce.challenge_for(v) == expected
    assert _pkce.challenge_for(v) != v


def test_the_authorize_url_carries_every_required_parameter():
    url = _pkce.authorize_url(
        subdomain="example",
        client_id="cid",
        redirect_uri="http://localhost:1234/callback",
        scopes=["tickets:read", "hc:read"],
        challenge="chal",
        state="st",
    )
    parsed = urlparse(url)
    assert parsed.netloc == "example.zendesk.com"
    assert parsed.path == "/oauth/authorizations/new"
    q = parse_qs(parsed.query)
    assert q["response_type"] == ["code"]
    assert q["client_id"] == ["cid"]
    assert q["code_challenge"] == ["chal"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["state"] == ["st"]
    assert q["scope"] == ["tickets:read hc:read"]
    assert "client_secret" not in q


def test_no_verifier_ever_reaches_the_url():
    v = _pkce.new_verifier()
    url = _pkce.authorize_url(
        subdomain="example",
        client_id="cid",
        redirect_uri="http://localhost:1/cb",
        scopes=["tickets:read"],
        challenge=_pkce.challenge_for(v),
        state="st",
    )
    assert v not in url
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/auth/test_pkce.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# src/csa_zendesk/auth/_pkce.py
"""PKCE, and the URL a browser is sent to.

Pure functions with no I/O, so the interesting property - that the verifier
never leaves this process - is testable directly rather than inferred.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from collections.abc import Sequence
from urllib.parse import urlencode


def new_verifier() -> str:
    """A fresh code verifier. 64 bytes of entropy, base64url, 86 characters."""
    return base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()


def challenge_for(verifier: str) -> str:
    """The S256 challenge. Never `plain`: ADR-009 makes S256 mandatory, and a
    `plain` challenge is the verifier itself, which defeats the point."""
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def authorize_url(
    *,
    subdomain: str,
    client_id: str,
    redirect_uri: str,
    scopes: Sequence[str],
    challenge: str,
    state: str,
) -> str:
    """The authorization URL. Takes the *challenge*, never the verifier - the
    verifier is the half that must not travel."""
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(scopes),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"https://{subdomain}.zendesk.com/oauth/authorizations/new?{query}"
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/auth/test_pkce.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/csa_zendesk/auth/_pkce.py tests/auth/test_pkce.py
git commit -m "feat(auth): PKCE S256 and the authorization URL"
```

---

> **Correction (2026-09-18), before execution.** Task 3 as written binds **port 0** — a different
> loopback port every run. `specs/zendesk-support-oas.yaml:30858` defines an OAuth client's
> `redirect_uri` as *"an array of the valid redirect URIs for this client"*, i.e. a **pre-registered
> list**. A dynamic port cannot match a pre-registered URI unless Zendesk implements RFC 8252's
> any-port loopback rule for native apps, and **we have not probed whether it does** — this repo's
> own standard is that an unprobed claim is a defect, not a fact.
>
> So `Listener` takes an ordered list of **candidate ports** and binds the first one free, rather than
> binding 0. The operator registers those exact URIs plus the out-of-band URN, and the paste fallback
> covers both the remote-shell case and the case where every candidate port is occupied:
>
> ```
> http://localhost:8765/callback
> http://localhost:8766/callback
> http://localhost:8767/callback
> urn:ietf:wg:oauth:2.0:oob
> ```
>
> `127.0.0.1` and `localhost` are **not** interchangeable to a string-matching authorization server —
> register whichever form the code sends, and send exactly what was registered. The plan's current
> code emits `http://127.0.0.1:{port}/callback`; the registration above uses `localhost`, so one of
> them must change and the tests must assert which.
>
> **Probe this before Task 3.** If Zendesk does honour any-port loopback, revert to binding 0 — it is
> the better design, because a fixed port is a port that can be occupied. Record the answer in
> `analysis/API-SURFACE.md` §7 either way, since it is a fact about the vendor and not about us.

> **Correction 2 (2026-09-18), before execution.** The correction above is superseded on two points
> by the text of Zendesk's own OAuth client form (now recorded verbatim in `analysis/API-SURFACE.md`
> §7.1): *"URLs must be absolute (not relative) and use HTTPS, unless they are for localhost or
> 127.0.0.1."*
>
> **1. The URN is not registrable, so there is no out-of-band flow.** `urn:ietf:wg:oauth:2.0:oob` is
> not an absolute URL and the form rejects it — this is the likely cause of the registration error
> that blocked this block. Task 3's paste fallback therefore cannot use the URN. It must pass a
> **registered loopback URI**, let the browser redirect fail to connect, and have the operator copy
> the `code` parameter out of the address bar. The `_run` sketch below still names the URN in two
> places; both become `PASTE_REDIRECT`, the first registered candidate URI, and the value sent to the
> token endpoint must be that same string.
>
> **2. Register `127.0.0.1`, not `localhost`** — RFC 8252 §8.3, and because `localhost` on a
> dual-stack host commonly resolves to `::1`, which never reaches a listener bound to IPv4. This
> matches what the plan's code already emits, so the code does not change; the earlier correction's
> registration list does. It becomes exactly:
>
> ```
> http://127.0.0.1:8765/callback
> http://127.0.0.1:8766/callback
> http://127.0.0.1:8767/callback
> ```
>
> Task 3's tests assert that every URI the code can emit is a member of that set, so the set is the
> single source of truth and a drifted port fails a test rather than an authorization.
>
> The any-port probe from correction 1 still stands and is still unanswered.

> **Correction 3 (2026-09-18), before execution.** The client is registered and the environment
> names are settled by what is already on disk rather than by this plan — every `CSA_ZENDESK_CLIENT_ID`
> above is now `CSA_ZENDESK_MCP_SERVER_IDENTIFIER`, and every `ZENDESK_SUBDOMAIN` is
> `CSA_ZENDESK_SUBDOMAIN`. Zendesk calls the client id the **Identifier**, so the name matches the
> field the operator actually reads off the screen.
>
> The registered scope ceiling is `read tickets:write ticket_attachments:write ticket_views:write`
> (`analysis/API-SURFACE.md` §7.2b). Task 4's existing `CSA_ZENDESK_SCOPES` default of `read` is
> therefore correct and deliberate: the ceiling is a cap, the request is the grant, and 0b brings the
> server up read-only. Widening is an environment change, not a re-registration.
>
> `CSA_ZENDESK_MCP_SERVER_SECRET` also exists on disk. It is **retained and unused** — Zendesk issues
> a secret to every client regardless of kind (§7.3), and whether refresh requires it is unprobed. No
> task in this block may read it; if the refresh probe says it is needed, that is an ADR-009
> correction first and a code change second.

### Task 3: The callback listener and the paste fallback

**Files:**
- Create: `src/csa_zendesk/auth/_callback.py`
- Test: `tests/auth/test_callback.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Listener` context manager with `.redirect_uri: str` and `.wait(timeout: float) -> str` returning the authorization code; `paste_fallback(prompt_to: TextIO, read_from: TextIO) -> str`; exception `CallbackError`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/auth/test_callback.py
import io
import threading
import urllib.request

import pytest
from csa_zendesk.auth import _callback


def test_it_binds_a_loopback_port_and_reports_it():
    with _callback.Listener(state="st") as listener:
        assert listener.redirect_uri.startswith("http://127.0.0.1:")
        assert listener.redirect_uri.endswith("/callback")


def test_it_returns_the_code_the_browser_delivers():
    with _callback.Listener(state="st") as listener:
        def deliver():
            urllib.request.urlopen(listener.redirect_uri + "?code=THE-CODE&state=st", timeout=5).read()

        threading.Thread(target=deliver, daemon=True).start()
        assert listener.wait(timeout=5) == "THE-CODE"


def test_a_mismatched_state_is_refused():
    # CSRF: a callback we did not initiate must not be accepted.
    with _callback.Listener(state="expected") as listener:
        def deliver():
            urllib.request.urlopen(listener.redirect_uri + "?code=X&state=attacker", timeout=5).read()

        threading.Thread(target=deliver, daemon=True).start()
        with pytest.raises(_callback.CallbackError, match="state"):
            listener.wait(timeout=5)


def test_an_error_response_is_surfaced_not_swallowed():
    with _callback.Listener(state="st") as listener:
        def deliver():
            urllib.request.urlopen(listener.redirect_uri + "?error=access_denied&state=st", timeout=5).read()

        threading.Thread(target=deliver, daemon=True).start()
        with pytest.raises(_callback.CallbackError, match="access_denied"):
            listener.wait(timeout=5)


def test_the_paste_fallback_reads_a_pasted_url_and_prompts_on_stderr():
    err = io.StringIO()
    stdin = io.StringIO("http://localhost/callback?code=PASTED&state=st\n")
    assert _callback.paste_fallback(prompt_to=err, read_from=stdin, state="st") == "PASTED"
    assert err.getvalue()  # the prompt exists
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/auth/test_callback.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# src/csa_zendesk/auth/_callback.py
"""Receiving the authorization code.

A one-shot loopback listener for the ordinary case, and a paste fallback for a
remote shell where no browser can reach this machine (ADR-009).

Every prompt goes to the stream the caller passes, which is stderr. Under stdio
MCP, stdout is the JSON-RPC channel: a print() here corrupts the session.
"""

from __future__ import annotations

import http.server
import threading
from types import TracebackType
from typing import TextIO
from urllib.parse import parse_qs, urlparse

from .. import exceptions as exc


class CallbackError(exc.ZendeskError):
    """The authorization callback did not deliver a usable code."""


_PAGE = b"<html><body><p>Authorised. You can close this tab.</p></body></html>"


class Listener:
    """A loopback HTTP server that accepts exactly one callback."""

    def __init__(self, state: str) -> None:
        self._state = state
        self._result: str | None = None
        self._error: str | None = None
        self._done = threading.Event()
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib's name
                q = parse_qs(urlparse(self.path).query)
                got_state = (q.get("state") or [""])[0]
                if "error" in q:
                    outer._error = (q.get("error") or [""])[0]
                elif got_state != outer._state:
                    outer._error = "state-mismatch"
                else:
                    outer._result = (q.get("code") or [""])[0] or None
                    if outer._result is None:
                        outer._error = "no-code"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(_PAGE)
                outer._done.set()

            def log_message(self, *args: object) -> None:
                """Silence the stdlib's stderr access log - it is noise, and the
                request line contains the authorization code."""

        self._server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}/callback"

    def __enter__(self) -> Listener:
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._server.shutdown()
        self._server.server_close()

    def wait(self, timeout: float) -> str:
        if not self._done.wait(timeout):
            raise CallbackError(
                f"no callback arrived within {timeout:.0f}s. If this machine has no "
                f"browser, re-run with --paste and paste the redirect URL instead."
            )
        if self._error == "state-mismatch":
            raise CallbackError("the callback carried the wrong state parameter; refusing it")
        if self._error:
            raise CallbackError(f"Zendesk refused the authorization: {self._error}")
        assert self._result is not None
        return self._result


def paste_fallback(*, prompt_to: TextIO, read_from: TextIO, state: str) -> str:
    """Read the redirect URL the operator pasted. For remote shells."""
    prompt_to.write("Paste the full redirect URL from the browser: ")
    prompt_to.flush()
    line = read_from.readline().strip()
    q = parse_qs(urlparse(line).query)
    if (q.get("state") or [""])[0] != state:
        raise CallbackError("the pasted URL carried the wrong state parameter; refusing it")
    code = (q.get("code") or [""])[0]
    if not code:
        raise CallbackError("the pasted URL has no `code` parameter")
    return code
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/auth/test_callback.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/csa_zendesk/auth/_callback.py tests/auth/test_callback.py
git commit -m "feat(auth): one-shot loopback callback, with a paste fallback"
```

---

### Task 4: Code exchange and scope verification

**Files:**
- Create: `src/csa_zendesk/auth/_flow.py`
- Test: `tests/auth/test_flow.py`

**Interfaces:**
- Consumes: `_store.Tokens`, `_store.write`, `_pkce.*`.
- Produces: `exchange_code(*, subdomain, client_id, code, verifier, redirect_uri, requested_scopes, transport=None) -> Tokens`, exception `ScopeError`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/auth/test_flow.py
import httpx
import pytest
from csa_zendesk.auth import _flow


def _transport(handler):
    return httpx.MockTransport(handler)


def test_the_exchange_posts_the_verifier_and_no_secret():
    seen = {}

    def handler(request):
        seen["body"] = dict(httpx.QueryParams(request.content.decode()))
        return httpx.Response(200, json={
            "access_token": "AT", "refresh_token": "RT",
            "expires_in": 1800, "scope": "tickets:read",
        })

    _flow.exchange_code(
        subdomain="example", client_id="cid", code="C", verifier="V",
        redirect_uri="http://127.0.0.1:1/cb", requested_scopes=["tickets:read"],
        transport=_transport(handler),
    )
    assert seen["body"]["code_verifier"] == "V"
    assert seen["body"]["grant_type"] == "authorization_code"
    assert "client_secret" not in seen["body"]


def test_a_granted_scope_narrower_than_requested_is_a_loud_error():
    # API-SURFACE §7: Zendesk issues a token for an unrecognised scope name and
    # then 403s every request made with it, so a typo produces a credential that
    # looks valid and works for nothing. Compare requested against granted.
    def handler(request):
        return httpx.Response(200, json={
            "access_token": "AT", "refresh_token": "RT",
            "expires_in": 1800, "scope": "tickets:read",
        })

    with pytest.raises(_flow.ScopeError, match="hc:write"):
        _flow.exchange_code(
            subdomain="example", client_id="cid", code="C", verifier="V",
            redirect_uri="http://127.0.0.1:1/cb",
            requested_scopes=["tickets:read", "hc:write"],
            transport=_transport(handler),
        )


def test_expires_at_is_absolute_not_relative(monkeypatch):
    monkeypatch.setattr(_flow.time, "time", lambda: 1_000.0)

    def handler(request):
        return httpx.Response(200, json={
            "access_token": "AT", "refresh_token": "RT",
            "expires_in": 1800, "scope": "tickets:read",
        })

    t = _flow.exchange_code(
        subdomain="example", client_id="cid", code="C", verifier="V",
        redirect_uri="http://127.0.0.1:1/cb", requested_scopes=["tickets:read"],
        transport=_transport(handler),
    )
    assert t.expires_at == 2_800.0


def test_no_credential_appears_in_an_exchange_failure_message():
    def handler(request):
        return httpx.Response(400, json={"error": "invalid_grant"})

    with pytest.raises(Exception) as ei:
        _flow.exchange_code(
            subdomain="example", client_id="cid", code="SECRET-CODE", verifier="SECRET-VERIFIER",
            redirect_uri="http://127.0.0.1:1/cb", requested_scopes=["tickets:read"],
            transport=_transport(handler),
        )
    assert "SECRET-CODE" not in str(ei.value)
    assert "SECRET-VERIFIER" not in str(ei.value)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/auth/test_flow.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement the exchange**

```python
# src/csa_zendesk/auth/_flow.py
"""Talking to Zendesk's OAuth endpoints. The only module here that makes requests."""

from __future__ import annotations

import time
from collections.abc import Sequence

import httpx

from .. import exceptions as exc
from ._store import Tokens


class ScopeError(exc.ZendeskError):
    """Zendesk granted fewer scopes than were asked for."""


class AuthExchangeError(exc.ZendeskError):
    """The OAuth endpoint refused a grant."""


def _post(subdomain: str, body: dict[str, str], transport: httpx.BaseTransport | None) -> dict[str, object]:
    with httpx.Client(transport=transport, timeout=30.0) as c:
        r = c.post(f"https://{subdomain}.zendesk.com/oauth/tokens", data=body)
    if r.status_code >= 400:
        # The body is echoed but never the request: `body` holds the code and the
        # verifier, and an exception message is the first place a credential leaks.
        raise AuthExchangeError(
            f"Zendesk refused the grant (HTTP {r.status_code}): {r.text[:200]}"
        )
    return dict(r.json())


def _to_tokens(payload: dict[str, object], requested: Sequence[str]) -> Tokens:
    granted = set(str(payload.get("scope", "")).split())
    missing = sorted(set(requested) - granted)
    if missing:
        raise ScopeError(
            f"Zendesk granted {sorted(granted)} but not {missing}. Zendesk issues a token "
            f"for an unrecognised scope name and then refuses every request made with it, "
            f"so check those names against the 54 documented scopes before retrying."
        )
    return Tokens(
        access_token=str(payload["access_token"]),
        refresh_token=str(payload["refresh_token"]),
        expires_at=time.time() + float(payload["expires_in"]),
    )


def exchange_code(
    *,
    subdomain: str,
    client_id: str,
    code: str,
    verifier: str,
    redirect_uri: str,
    requested_scopes: Sequence[str],
    transport: httpx.BaseTransport | None = None,
) -> Tokens:
    payload = _post(
        subdomain,
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
            "scope": " ".join(requested_scopes),
        },
        transport,
    )
    return _to_tokens(payload, requested_scopes)
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/auth/test_flow.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/csa_zendesk/auth/_flow.py tests/auth/test_flow.py
git commit -m "feat(auth): code exchange, and refuse a token whose scopes were not granted"
```

---

### Task 5: Refresh, and the `access_token()` provider

**Files:**
- Modify: `src/csa_zendesk/auth/_flow.py`
- Test: `tests/auth/test_refresh.py`

**Interfaces:**
- Consumes: Task 4's `_post`, `_to_tokens`; Task 1's `read`/`write`.
- Produces: `refresh(*, subdomain, client_id, tokens, requested_scopes, transport=None) -> Tokens`, `access_token(*, subdomain=None, client_id=None, now=time.time) -> str`, constant `REFRESH_MARGIN_SECONDS = 120`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/auth/test_refresh.py
import httpx
import pytest
from csa_zendesk.auth import _flow, _store


def _ok(handler_calls):
    def handler(request):
        handler_calls.append(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(200, json={
            "access_token": "NEW-AT", "refresh_token": "NEW-RT",
            "expires_in": 1800, "scope": "tickets:read",
        })
    return httpx.MockTransport(handler)


def test_a_token_inside_the_margin_is_refreshed_before_it_expires(monkeypatch, tmp_path):
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "t.json"))
    monkeypatch.setenv("CSA_ZENDESK_SUBDOMAIN", "example")
    monkeypatch.setenv("CSA_ZENDESK_MCP_SERVER_IDENTIFIER", "cid")
    # Expires in 60s; the margin is 120s, so this must refresh rather than return it.
    _store.write(_store.Tokens("OLD-AT", "OLD-RT", 1_060.0))
    calls: list[dict] = []
    monkeypatch.setattr(_flow.time, "time", lambda: 1_000.0)
    assert _flow.access_token(transport=_ok(calls)) == "NEW-AT"
    assert calls[0]["grant_type"] == "refresh_token"
    assert _store.read().refresh_token == "NEW-RT"  # rotation is persisted


def test_a_healthy_token_is_returned_without_a_request(monkeypatch, tmp_path):
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "t.json"))
    monkeypatch.setenv("CSA_ZENDESK_SUBDOMAIN", "example")
    monkeypatch.setenv("CSA_ZENDESK_MCP_SERVER_IDENTIFIER", "cid")
    _store.write(_store.Tokens("GOOD-AT", "RT", 9_999.0))
    monkeypatch.setattr(_flow.time, "time", lambda: 1_000.0)

    def explode(request):  # pragma: no cover - must never be reached
        raise AssertionError("refreshed a token that had not expired")

    assert _flow.access_token(transport=httpx.MockTransport(explode)) == "GOOD-AT"


def test_no_token_file_says_how_to_fix_it(monkeypatch, tmp_path):
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "absent.json"))
    monkeypatch.setenv("CSA_ZENDESK_SUBDOMAIN", "example")
    monkeypatch.setenv("CSA_ZENDESK_MCP_SERVER_IDENTIFIER", "cid")
    with pytest.raises(_flow.NotAuthorised, match="csa-zendesk auth login"):
        _flow.access_token()


def test_a_missing_client_id_is_refused_with_no_default(monkeypatch, tmp_path):
    # ADR-009 rejected embedding a CSA client id: every deployment would share one
    # client's scope ceiling and rate-limit attribution.
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "t.json"))
    monkeypatch.setenv("CSA_ZENDESK_SUBDOMAIN", "example")
    monkeypatch.delenv("CSA_ZENDESK_MCP_SERVER_IDENTIFIER", raising=False)
    _store.write(_store.Tokens("AT", "RT", 9_999.0))
    with pytest.raises(_flow.NotAuthorised, match="CSA_ZENDESK_MCP_SERVER_IDENTIFIER"):
        _flow.access_token()
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/auth/test_refresh.py -v`
Expected: FAIL — `AttributeError: module has no attribute 'access_token'`

- [ ] **Step 3: Implement refresh and the provider**

```python
# append to src/csa_zendesk/auth/_flow.py
import os

from . import _store

#: Refresh this many seconds before the access token actually expires. Zendesk
#: applies a 30-minute expires_in automatically to clients created on or after
#: 2026-04-30, so the margin is generous relative to a request but small relative
#: to the lifetime.
REFRESH_MARGIN_SECONDS = 120


class NotAuthorised(exc.ZendeskError):
    """No usable credential. The operator has to do something."""


def refresh(
    *,
    subdomain: str,
    client_id: str,
    tokens: Tokens,
    requested_scopes: Sequence[str],
    transport: httpx.BaseTransport | None = None,
) -> Tokens:
    payload = _post(
        subdomain,
        {
            "grant_type": "refresh_token",
            "refresh_token": tokens.refresh_token,
            "client_id": client_id,
            "scope": " ".join(requested_scopes),
        },
        transport,
    )
    fresh = _to_tokens(payload, requested_scopes)
    _store.write(fresh)  # refresh ROTATES the refresh token; losing it means re-login
    return fresh


def access_token(*, transport: httpx.BaseTransport | None = None) -> str:
    """A currently-valid access token. THE accessor.

    Called on every request (`HttpClient`'s `token_provider`), so the healthy path
    is a file read and a float comparison with no network at all.
    """
    subdomain = os.environ.get("CSA_ZENDESK_SUBDOMAIN", "")
    client_id = os.environ.get("CSA_ZENDESK_MCP_SERVER_IDENTIFIER", "")
    if not client_id:
        raise NotAuthorised(
            "CSA_ZENDESK_MCP_SERVER_IDENTIFIER is not set. Register a public OAuth client in "
            "Zendesk Admin Center (no secret is needed) and set its id. There is no "
            "default client id, deliberately: a shared one would pool every "
            "deployment's rate limit and scope ceiling."
        )
    tokens = _store.read()
    if tokens is None:
        raise NotAuthorised("no token file. Run `csa-zendesk auth login` first.")
    if tokens.expires_at - time.time() > REFRESH_MARGIN_SECONDS:
        return tokens.access_token
    scopes = sorted(set(os.environ.get("CSA_ZENDESK_SCOPES", "read").split()))
    return refresh(
        subdomain=subdomain, client_id=client_id, tokens=tokens,
        requested_scopes=scopes, transport=transport,
    ).access_token
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/auth/test_refresh.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/csa_zendesk/auth/_flow.py tests/auth/test_refresh.py
git commit -m "feat(auth): refresh before expiry, and persist the rotated refresh token"
```

---

### Task 6: Refresh on rejection — but only on `invalid_token`

**Files:**
- Modify: `src/csa_zendesk/_http.py`
- Test: `tests/test_http.py`

**Interfaces:**
- Consumes: Task 5's `access_token`.
- Produces: `HttpClient(..., on_invalid_token: Callable[[], None] | None = None)` — called once before a single retry.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_http.py
def test_a_401_with_invalid_token_refreshes_once_and_retries():
    # ADR-009: retried once, and ONLY on invalid_token.
    calls = {"n": 0, "refreshed": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(401, json={"error": "invalid_token"})
        return httpx.Response(200, json={"ok": True})

    c = HttpClient(
        subdomain="example",
        token_provider=lambda: CANARY,
        transport=httpx.MockTransport(handler),
        on_invalid_token=lambda: calls.__setitem__("refreshed", calls["refreshed"] + 1),
    )
    assert c.get("/api/v2/tickets.json") == {"ok": True}
    assert calls["refreshed"] == 1
    assert calls["n"] == 2


def test_a_401_from_a_scope_problem_is_passed_through_unchanged():
    # Blanket-retrying 401/403 would mask a scope misconfiguration as a transient
    # fault - and Zendesk issues tokens for unrecognised scope names, so this is
    # the common case, not the rare one.
    refreshed = []

    def handler(request):
        return httpx.Response(401, json={"error": "insufficient_scope"})

    c = HttpClient(
        subdomain="example",
        token_provider=lambda: CANARY,
        transport=httpx.MockTransport(handler),
        on_invalid_token=lambda: refreshed.append(1),
    )
    with pytest.raises(exc.CredentialsRejected):
        c.get("/api/v2/tickets.json")
    assert refreshed == []


def test_a_second_invalid_token_is_not_retried_again():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(401, json={"error": "invalid_token"})

    c = HttpClient(
        subdomain="example",
        token_provider=lambda: CANARY,
        transport=httpx.MockTransport(handler),
        on_invalid_token=lambda: None,
    )
    with pytest.raises(exc.CredentialsRejected):
        c.get("/api/v2/tickets.json")
    assert calls["n"] == 2  # the original and exactly one retry
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_http.py -k invalid_token -v`
Expected: FAIL — `TypeError: unexpected keyword argument 'on_invalid_token'`

- [ ] **Step 3: Implement**

In `HttpClient.__init__`, accept and store `on_invalid_token`. In the request path, after the response is received and before `parse_error` raises:

```python
        # ADR-009: refresh on rejection, retried ONCE, and only when Zendesk says
        # `invalid_token`. A 401 or 403 arising from scope or from the operator's
        # own Zendesk permissions is passed through unchanged, so a permissions
        # problem stays visible as a permissions problem rather than looking like
        # auth flakiness.
        if (
            response.status_code == 401
            and not retried_auth
            and self._on_invalid_token is not None
            and _is_invalid_token(response)
        ):
            retried_auth = True
            self._on_invalid_token()
            continue
```

with:

```python
def _is_invalid_token(response: httpx.Response) -> bool:
    """True only for Zendesk's `invalid_token`, never for a scope or permission 401."""
    try:
        body = response.json()
    except ValueError:
        return False
    return isinstance(body, dict) and body.get("error") == "invalid_token"
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_http.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/csa_zendesk/_http.py tests/test_http.py
git commit -m "feat(http): refresh once on invalid_token, and never on a scope 401"
```

---

### Task 7: `login` and `whoami`

**Files:**
- Create: `src/csa_zendesk/auth/whoami.py`
- Modify: `src/csa_zendesk/auth/__init__.py`
- Test: `tests/auth/test_whoami.py`

**Interfaces:**
- Consumes: Tasks 1–5.
- Produces: `login(*, scopes, open_browser=True, paste=False) -> Tokens`, `whoami(*, transport=None) -> dict[str, object]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/auth/test_whoami.py
import httpx
import pytest
from csa_zendesk.auth import whoami as w


def test_an_anonymous_user_object_is_treated_as_unauthenticated():
    # CLAUDE.md invariant 1: users/me.json answers HTTP 200 with an "Anonymous
    # user" object when wholly unauthenticated. A naive whoami reports success.
    def handler(request):
        return httpx.Response(200, json={"user": {"id": None, "name": "Anonymous user", "role": "end-user"}})

    with pytest.raises(w.NotAuthenticated, match="Anonymous"):
        w.whoami(transport=httpx.MockTransport(handler))


def test_a_real_identity_is_returned():
    def handler(request):
        return httpx.Response(200, json={"user": {"id": 42, "name": "Agent", "role": "admin"}})

    who = w.whoami(transport=httpx.MockTransport(handler))
    assert who["id"] == 42 and who["role"] == "admin"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/auth/test_whoami.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# src/csa_zendesk/auth/whoami.py
"""Who the credential actually is.

`users/me.json` answers **200 with an "Anonymous user" object** when wholly
unauthenticated (CLAUDE.md invariant 1), so a status check is not an identity
check. This asserts on the body.
"""

from __future__ import annotations

import httpx

from .. import exceptions as exc
from ._flow import access_token


class NotAuthenticated(exc.ZendeskError):
    """The credential reached Zendesk and Zendesk did not recognise it."""


def whoami(*, subdomain: str | None = None, transport: httpx.BaseTransport | None = None) -> dict[str, object]:
    import os

    sub = subdomain or os.environ.get("CSA_ZENDESK_SUBDOMAIN", "")
    with httpx.Client(transport=transport, timeout=30.0) as c:
        r = c.get(
            f"https://{sub}.zendesk.com/api/v2/users/me.json",
            headers={"Authorization": f"Bearer {access_token()}", "Accept": "application/json"},
        )
    user = dict(r.json().get("user") or {})
    if user.get("id") is None or user.get("name") == "Anonymous user":
        raise NotAuthenticated(
            "Zendesk answered 200 with an Anonymous user object, which is what it "
            "returns for an unauthenticated request. The token is not being sent or "
            "is not recognised. Run `csa-zendesk auth login`."
        )
    return user
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/auth/test_whoami.py -v`
Expected: 2 passed

- [ ] **Step 5: Wire `login()` into `auth/__init__.py`**

```python
# src/csa_zendesk/auth/__init__.py
"""OAuth. ADR-009 (the flow) and ADR-015 (it is the only one)."""

from __future__ import annotations

import os
import secrets
import sys
import webbrowser
from collections.abc import Sequence

from ._callback import CallbackError, Listener, paste_fallback
from ._flow import NotAuthorised, ScopeError, access_token, refresh
from ._pkce import authorize_url, challenge_for, new_verifier
from ._store import Tokens, TokenFileError, clear, read, token_path, write

__all__ = [
    "CallbackError", "NotAuthorised", "ScopeError", "TokenFileError",
    "Tokens", "access_token", "clear", "login", "token_path", "whoami",
]


def login(*, scopes: Sequence[str], paste: bool = False, timeout: float = 300.0) -> Tokens:
    """Run the browser flow once and persist the result."""
    from ._flow import exchange_code
    from .whoami import whoami as _whoami  # noqa: F401 - re-exported below

    subdomain = os.environ["CSA_ZENDESK_SUBDOMAIN"]
    client_id = os.environ["CSA_ZENDESK_MCP_SERVER_IDENTIFIER"]
    verifier, state = new_verifier(), secrets.token_urlsafe(16)

    if paste:
        url = authorize_url(
            subdomain=subdomain, client_id=client_id,
            redirect_uri="urn:ietf:wg:oauth:2.0:oob", scopes=scopes,
            challenge=challenge_for(verifier), state=state,
        )
        # stderr, never stdout: stdout is the MCP JSON-RPC channel.
        print(f"Open this URL:\n{url}\n", file=sys.stderr)
        code = paste_fallback(prompt_to=sys.stderr, read_from=sys.stdin, state=state)
        redirect = "urn:ietf:wg:oauth:2.0:oob"
    else:
        with Listener(state=state) as listener:
            url = authorize_url(
                subdomain=subdomain, client_id=client_id,
                redirect_uri=listener.redirect_uri, scopes=scopes,
                challenge=challenge_for(verifier), state=state,
            )
            print(f"Opening your browser to authorise:\n{url}\n", file=sys.stderr)
            webbrowser.open(url)
            code = listener.wait(timeout)
            redirect = listener.redirect_uri

    tokens = exchange_code(
        subdomain=subdomain, client_id=client_id, code=code, verifier=verifier,
        redirect_uri=redirect, requested_scopes=list(scopes),
    )
    write(tokens)
    return tokens


from .whoami import NotAuthenticated, whoami  # noqa: E402 - avoids a circular import
```

- [ ] **Step 6: Run the whole suite**

Run: `python -m pytest -q --cov=csa_zendesk --cov-fail-under=100`
Expected: all pass, 100%

- [ ] **Step 7: Commit**

```bash
git add src/csa_zendesk/auth/ tests/auth/
git commit -m "feat(auth): login flow and a whoami that does not trust a 200"
```

---

### Task 8: Port `scripts/` and delete API-token support

**Files:**
- Modify: `scripts/zd.py`
- Test: manual, against the live API — these are scripts and have no suite.

**Interfaces:**
- Consumes: `csa_zendesk.auth.access_token`.
- Produces: `zd.authorize(req)` sending `Bearer`; `zd.missing_credentials()` reporting OAuth configuration.

- [ ] **Step 1: Replace the body of `authorize` and `missing_credentials`**

```python
def missing_credentials() -> list[str]:
    """Configuration that is not set. OAuth needs a subdomain and a client id;
    the tokens themselves live in the token file, not the environment."""
    return [n for n in ("CSA_ZENDESK_SUBDOMAIN", "CSA_ZENDESK_MCP_SERVER_IDENTIFIER") if not os.environ.get(n)]


def authorize(req: urllib.request.Request) -> None:
    """Attach credentials to a request. THE auth chokepoint for every script.

    Scripts authenticate exactly as the library does (ADR-015): same token file,
    same refresh, same failure modes. That is the point - the probes are the first
    consumer of this flow, so if it is wrong here we find out before a tool
    depends on it.
    """
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
    from csa_zendesk.auth import access_token

    req.add_header("Authorization", f"Bearer {access_token()}")
    req.add_header("Accept", "application/json")
```

- [ ] **Step 2: Delete every API-token reference**

Remove `_auth()`, the `base64` import if now unused, and confirm:

```bash
grep -rn "CINO_CSA_ZENDESK" scripts/ src/ tests/ && echo "STILL PRESENT - fix" || echo "clean"
```

Expected: `clean`

- [ ] **Step 3: Verify against the live API**

```bash
export CSA_ZENDESK_SUBDOMAIN=... CSA_ZENDESK_MCP_SERVER_IDENTIFIER=...
python3 -c "import sys; sys.path.insert(0,'scripts'); import zd; print(zd.call('GET','/api/v2/ticket_fields.json')[0])"
```

Expected: `200`

- [ ] **Step 4: Run every probe end to end**

```bash
python3 scripts/probe_families.py && python3 scripts/probe_access.py && python3 scripts/ui_actions.py
```

Expected: all succeed. These are long-running and hit hundreds of endpoints, which is what exercises refresh mid-run — the reason ADR-015's amendment made the scripts OAuth's first consumer.

- [ ] **Step 5: Commit**

```bash
git add scripts/
git commit -m "refactor(scripts): authenticate by OAuth, and delete the API-token path"
```

---

### Task 9: Reconcile the documents ADR-009 said contradict each other

**Files:**
- Modify: `DECISIONS-ADR/ADR-005.md`, `CLAUDE.md`, `SECURITY.md`, `README.md`, `TODO.md`, `WAITING-FOR.md`

- [ ] **Step 1: Amend ADR-005 in place**

Append, struck through rather than rewritten:

> **Amendment (ADR-009, implemented Block 0b):** "no persistence" means **no response persistence and no attachment cache**. One token file is excluded, because refresh tokens require it and a credential is not customer data. See `DECISIONS-ADR/ADR-009.md`.

- [ ] **Step 2: Correct `CLAUDE.md`**

Replace the flat *"no token file"* with the scoped form `SECURITY.md` already uses, and point at ADR-009.

- [ ] **Step 3: Update the credentials sections**

`README.md` and `CLAUDE.md`: the environment carries `CSA_ZENDESK_SUBDOMAIN`, `CSA_ZENDESK_MCP_SERVER_IDENTIFIER` and optionally `CSA_ZENDESK_SCOPES`. It carries **no credential**. `./.env` stops being a credential source.

- [ ] **Step 4: Close the tracked items**

- `TODO.md` **B11** → done, linking this plan.
- `TODO.md` **D10** and **D11** → done.
- `WAITING-FOR-003` → **Resolved**, with the date OAuth first authenticated against the live account.

- [ ] **Step 5: Run the publication gate and commit**

```bash
python3 scripts/check_public_safe.py
git add -A
git commit -m "docs: ADR-005 amended, and the environment no longer carries a credential"
```

---

## Self-Review

**Spec coverage.** ADR-009's decisions map to tasks as follows: public client + PKCE S256 → Task 2; token file path, `0600`/`0700`, atomic write, three fields only → Task 1; mode asserted on every read → Task 1 Step 7; refresh before expiry → Task 5; refresh on rejection, once, only on `invalid_token`, with scope 401/403 passed through → Task 6; no embedded client id → Task 5's `NotAuthorised`; localhost listener with paste fallback → Task 3; credential never in a log, message or `__repr__` → asserted in Tasks 1, 2 and 4. ADR-015: no API-token path anywhere → Task 8, with a `grep` that fails the step if one survives. The scope-typo trap from API-SURFACE §7 → Task 4.

**One spec item deliberately deferred.** ADR-009 specifies the atomic write happens *"under a lock file"*. Tasks 1 and 5 implement the atomic rename but not the lock. `os.replace` is atomic on POSIX, so concurrent writers cannot produce a torn file — the residual risk is two overlapping refreshes losing one rotated refresh token, which costs a re-login rather than corruption. **Filed as a TODO item in Task 9 rather than built**, because a correct cross-platform lock is more code than the rest of the store and the failure it prevents is recoverable. Revisit when the server runs under more than one MCP client in practice.

**Placeholder scan.** No `TBD`, no "add error handling", no "similar to Task N". Every code step carries the code.

**Type consistency.** `Tokens(access_token, refresh_token, expires_at)` is constructed in Tasks 1, 4 and 5 with those names throughout. `access_token()` the function and `Tokens.access_token` the field share a name — deliberate and consistent with the spec's language, and they never appear in the same scope. `transport` is the injection point in every module that makes requests.
