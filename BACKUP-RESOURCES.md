# Backup resources

Exposure, access model and accepted risks are in
[`SECURITY-RESOURCES.md`](SECURITY-RESOURCES.md). This file answers only the backup question —
and here that question has a real answer rather than an empty one, because unlike its siblings
this server **does** persist something.

## The only persistent state is a credential

```
$CSA_ZENDESK_TOKEN_FILE, or $XDG_CONFIG_HOME/csa-zendesk/tokens.json,
or ~/.config/csa-zendesk/tokens.json
```

Everything else is source, and git is its recovery story.

**The correct retention policy for that file is zero copies**, which inverts the usual question.
Losing it costs one `authenticate` call — a Zendesk OAuth token is re-issuable on demand, so
there is nothing a backup would save you from. Copying it costs a credential in a second place
that nothing is watching. So: **do not back it up, and treat any system that already has is a
disclosure to assess**, not a safety net.

Two specific cases worth knowing about:

- **`CSA_ZENDESK_TOKEN_FILE` can point anywhere.** `_store.py`'s own comment says so —
  *"`/var/lib/myservice/zd.json` or `$HOME/zd.json` are both legal values"*. Pointing it inside a
  folder synced by OneDrive, Dropbox or iCloud puts the token in a vendor's storage and in every
  device's copy of it. Nothing in the code can detect that, and nothing should refuse it; it is a
  operator decision that needs stating rather than guarding.
- **The default path is not synced by default** on either platform — `~/.config` on POSIX, and
  on Windows `~/.config` sits outside the Desktop/Documents/Pictures set OneDrive backs up unless
  the operator has widened it.

## What protects it, and the gap that is worth this file existing

The protection is real and carefully built: the directory is not blanket-`chmod`ed (an earlier
version ran `path.parent.chmod(0o700)` on every refresh, which would have silently narrowed
`$HOME` or `/var/lib/myservice`), the file is written `0600`, and on Windows the ACL is read with
`icacls` and unexpected principals are reported rather than assumed absent. `_store.py` even
handles *unknown* separately from *insecure*, with the comment **"UNKNOWN IS NOT SECURE, and it
is also not a finding"** — refusing there would mean a machine whose ACL tool is unavailable can
never log in.

**And that machinery is the least-tested code in the repo on the platform where it matters
most.** Measured on Windows, 2026-10-02, by re-running the suite with the `# pragma: no cover`
exclusions disabled:

| | stmts | miss | cover |
|---|---|---|---|
| `auth/_privacy.py`, pragmas **on** | 33 | 7 | 77% |
| `auth/_privacy.py`, pragmas **off** | 93 | 17 | **74%** |

The second row is the one to read. `_read_acl`, `_windows_is_private`,
`_windows_explicitly_hardened` and `unexpected_principals` are `# pragma: no cover` on ubuntu
because they *cannot* run there — and **on Windows, where they can, the suite still does not
execute 26% of the module.** Excluded on one platform, unexecuted on the other, so measured
nowhere.

So the file guarding this project's only persistent credential has its platform-specific half
unverified on both platforms. That is CINO-Platform-Engineering#172, where the numbers above are
recorded, and it is the reason this file is not a formality.

Not a claim that the protection is wrong — reviewing it, it is better than most. A claim that
**nothing currently demonstrates it still works**, which is a different statement and the one
`TESTING.md` cares about.

## Elsewhere

- Exposure surface, access model, data classification, accepted risks:
  [`SECURITY-RESOURCES.md`](SECURITY-RESOURCES.md).
- What the repo must never contain, and the guard that enforces it:
  [`OPERATIONAL-RESOURCES.md`](OPERATIONAL-RESOURCES.md).
- `os.chmod` honouring only the read-only bit, and the other calls that run and lie:
  [`POSIX-AND-WINDOWS.md`](https://github.com/CloudSecurityAlliance-Internal/CINO-Platform-Engineering/blob/main/POSIX-AND-WINDOWS.md).
