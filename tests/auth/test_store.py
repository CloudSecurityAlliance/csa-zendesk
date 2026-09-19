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


def test_write_creates_0600_in_a_0700_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "d" / "tokens.json"))
    _store.write(_store.Tokens("at", "rt", 1000.0, "read"))
    p = _store.token_path()
    import stat

    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert stat.S_IMODE(p.parent.stat().st_mode) == 0o700


def test_the_file_holds_exactly_four_fields(monkeypatch, tmp_path):
    # ADR-009 said "the refresh token, the access token and its expiry. Nothing
    # else. No ticket data." Task 5 added `scope` (a corrected ADR-009 follows,
    # per its own fix report) - not a credential and not response/ticket data,
    # but it is a fourth field, so the letter of the old sentence needed an
    # update. What the sentence protects - no ticket data, no response payload -
    # is unchanged; this test now enforces the corrected boundary.
    import json

    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "tokens.json"))
    _store.write(_store.Tokens("at", "rt", 1000.0, "read"))
    raw = json.loads(_store.token_path().read_text())
    assert set(raw) == {"access_token", "refresh_token", "expires_at", "scope"}


def test_a_world_readable_file_is_a_loud_error_not_a_warning(monkeypatch, tmp_path):
    # ADR-009: "a 0644 token file is a finding, not a preference."
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "tokens.json"))
    _store.write(_store.Tokens("at", "rt", 1000.0, "read"))
    _store.token_path().chmod(0o644)
    with pytest.raises(_store.TokenFileError, match="0644"):
        _store.read()


def test_read_returns_none_when_there_is_no_file(monkeypatch, tmp_path):
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "absent.json"))
    assert _store.read() is None


def test_a_dangling_symlink_is_not_reported_as_no_token_file(monkeypatch, tmp_path):
    # path.exists() is False for a symlink whose target is gone - the same
    # return value as "no token file at all" - which would make `auth status`
    # print "no token file, run auth login" for a dangling symlink instead of
    # naming the actual problem.
    target = tmp_path / "nonexistent-target.json"
    link = tmp_path / "tokens.json"
    link.symlink_to(target)
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(link))
    with pytest.raises(_store.TokenFileError, match="symlink"):
        _store.read()


def test_an_os_error_reading_the_file_is_a_token_file_error_not_a_traceback(monkeypatch, tmp_path):
    # A PermissionError (or any other OSError) must be reported the same way
    # every other unreadable-token-file case is, not escape as a raw
    # traceback from `auth status` - the one command whose job is to report
    # what state you are in.
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "tokens.json"))
    _store.write(_store.Tokens("at", "rt", 1000.0, "read"))

    def raise_permission_error(self):
        raise PermissionError("denied")

    monkeypatch.setattr(_store.pathlib.Path, "read_text", raise_permission_error)
    with pytest.raises(_store.TokenFileError, match="not a readable token file"):
        _store.read()


def test_an_existing_directory_we_did_not_create_is_verified_not_chmodded(monkeypatch, tmp_path):
    # Regression: `_ensure_dir` used to `chmod(0o700)` the token file's parent
    # directory unconditionally, on every write - i.e. every refresh -
    # regardless of who created it or what else lives there.
    # CSA_ZENDESK_TOKEN_FILE=/var/lib/myservice/zd.json would silently reduce
    # that shared directory to owner-only. A pre-existing, correctly-private
    # directory must be left exactly as it was: verified, not touched.
    directory = tmp_path / "shared"
    directory.mkdir()
    directory.chmod(0o700)  # explicit, not relying on umask to land exactly here
    before = directory.stat().st_mode
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(directory / "tokens.json"))
    _store.write(_store.Tokens("at", "rt", 1000.0, "read"))
    assert directory.stat().st_mode == before


def test_an_existing_directory_looser_than_0700_is_refused_not_silently_fixed(monkeypatch, tmp_path):
    directory = tmp_path / "loose"
    directory.mkdir()
    directory.chmod(0o755)  # explicit, not relying on umask to land exactly here
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(directory / "tokens.json"))
    with pytest.raises(_store.TokenFileError, match="0755"):
        _store.write(_store.Tokens("at", "rt", 1000.0, "read"))


def test_intermediate_parents_created_along_the_way_are_also_private(monkeypatch, tmp_path):
    # mkdir(mode=0o700, parents=True) applies `mode` to the leaf directory
    # only - any intermediate parents it creates are left at the umask unless
    # each one created is chmodded too.
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "a" / "b" / "tokens.json"))
    _store.write(_store.Tokens("at", "rt", 1000.0, "read"))
    import stat

    assert stat.S_IMODE((tmp_path / "a").stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "a" / "b").stat().st_mode) == 0o700


def test_a_credential_never_appears_in_a_repr(monkeypatch, tmp_path):
    t = _store.Tokens("SECRET-ACCESS", "SECRET-REFRESH", 1.0, "tickets:read")
    assert "SECRET-ACCESS" not in repr(t)
    assert "SECRET-REFRESH" not in repr(t)
    assert "tickets:read" in repr(t)  # not a credential - fine to show


def test_read_raises_on_invalid_json(monkeypatch, tmp_path):
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "tokens.json"))
    (tmp_path / "tokens.json").write_text("{not valid json")
    (tmp_path / "tokens.json").chmod(0o600)
    with pytest.raises(_store.TokenFileError, match="not a readable token file"):
        _store.read()


def test_read_raises_on_missing_field(monkeypatch, tmp_path):
    import json

    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "tokens.json"))
    (tmp_path / "tokens.json").write_text(json.dumps({"access_token": "at"}))
    (tmp_path / "tokens.json").chmod(0o600)
    with pytest.raises(_store.TokenFileError, match="not a readable token file"):
        _store.read()


def test_read_raises_on_invalid_expires_at(monkeypatch, tmp_path):
    import json

    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "tokens.json"))
    (tmp_path / "tokens.json").write_text(
        json.dumps({"access_token": "at", "refresh_token": "rt", "expires_at": "not_a_number", "scope": "read"})
    )
    (tmp_path / "tokens.json").chmod(0o600)
    with pytest.raises(_store.TokenFileError, match="not a readable token file"):
        _store.read()


def test_read_raises_on_missing_scope(monkeypatch, tmp_path):
    # Decision: a file written before `scope` was tracked is REFUSED, not
    # silently treated as "no scope" - an empty grant is exactly the value
    # that made refresh's scope-narrowing check vacuous in the first place.
    # There is no migration path because nothing has authenticated against
    # this client yet - every such file is stale by construction.
    import json

    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "tokens.json"))
    (tmp_path / "tokens.json").write_text(
        json.dumps({"access_token": "at", "refresh_token": "rt", "expires_at": 1000.0})
    )
    (tmp_path / "tokens.json").chmod(0o600)
    with pytest.raises(_store.TokenFileError, match="not a readable token file"):
        _store.read()


def test_scope_round_trips_through_write_and_read(monkeypatch, tmp_path):
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "tokens.json"))
    _store.write(_store.Tokens("at", "rt", 1000.0, "read tickets:write"))
    assert _store.read().scope == "read tickets:write"


def test_clear_removes_file(monkeypatch, tmp_path):
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "tokens.json"))
    _store.write(_store.Tokens("at", "rt", 1000.0, "read"))
    assert _store.token_path().exists()
    _store.clear()
    assert not _store.token_path().exists()


def test_clear_succeeds_when_no_file(monkeypatch, tmp_path):
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "absent.json"))
    _store.clear()  # Should not raise


def test_write_cleans_up_temp_file_on_exception(monkeypatch, tmp_path):
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "tokens.json"))

    # A valid file already on disk - the state a crash mid-write must never
    # destroy. `write()` is atomic via `os.replace`, so a failure there must
    # leave this exact file behind: not truncated, not empty, not partially
    # overwritten with the new attempt's bytes.
    _store.write(_store.Tokens("old-at", "old-rt", 1000.0, "read"))
    before = _store.token_path().read_bytes()

    def failing_replace(src, dst):
        # Raise without touching either file, so the assertions below observe
        # write()'s own recovery - not behavior this mock performed for it.
        raise RuntimeError("Simulated failure")

    monkeypatch.setattr("os.replace", failing_replace)
    with pytest.raises(RuntimeError, match="Simulated failure"):
        _store.write(_store.Tokens("new-at", "new-rt", 2000.0, "read"))

    # The previously-valid file is untouched - the atomic-write guarantee.
    assert _store.token_path().read_bytes() == before
    # And write()'s own except-block unlinked the temp file it created,
    # rather than leaving an orphan behind.
    assert len(list(tmp_path.glob(".tokens-*"))) == 0
