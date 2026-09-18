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
    _store.write(_store.Tokens("at", "rt", 1000.0))
    p = _store.token_path()
    import stat

    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert stat.S_IMODE(p.parent.stat().st_mode) == 0o700


def test_the_file_holds_exactly_three_fields(monkeypatch, tmp_path):
    # ADR-009: "the refresh token, the access token and its expiry. Nothing else.
    # No ticket data." This test is the enforcement of that sentence.
    import json

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
        json.dumps({"access_token": "at", "refresh_token": "rt", "expires_at": "not_a_number"})
    )
    (tmp_path / "tokens.json").chmod(0o600)
    with pytest.raises(_store.TokenFileError, match="not a readable token file"):
        _store.read()


def test_clear_removes_file(monkeypatch, tmp_path):
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "tokens.json"))
    _store.write(_store.Tokens("at", "rt", 1000.0))
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
    _store.write(_store.Tokens("old-at", "old-rt", 1000.0))
    before = _store.token_path().read_bytes()

    def failing_replace(src, dst):
        # Raise without touching either file, so the assertions below observe
        # write()'s own recovery - not behavior this mock performed for it.
        raise RuntimeError("Simulated failure")

    monkeypatch.setattr("os.replace", failing_replace)
    with pytest.raises(RuntimeError, match="Simulated failure"):
        _store.write(_store.Tokens("new-at", "new-rt", 2000.0))

    # The previously-valid file is untouched - the atomic-write guarantee.
    assert _store.token_path().read_bytes() == before
    # And write()'s own except-block unlinked the temp file it created,
    # rather than leaving an orphan behind.
    assert len(list(tmp_path.glob(".tokens-*"))) == 0
