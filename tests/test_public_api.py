import inspect

import csa_zendesk


def test_version_is_a_dotted_string():
    assert isinstance(csa_zendesk.__version__, str)
    assert csa_zendesk.__version__.count(".") >= 2


def test_package_exports_nothing_it_has_not_declared():
    # __all__ is the contract. Anything public and undeclared is an accident.
    # A plain `not n.startswith("_")` filter would also exclude __version__ itself
    # (a dunder by convention, but the one deliberately public name here), so a
    # name counts as "public" if it isn't underscore-prefixed OR it is declared
    # in __all__.
    #
    # Importing a submodule binds it as an attribute of the parent package, so a
    # submodule's presence in dir() depends on which tests ran first. That is an import
    # artifact, not an export decision - __all__ plus Task 8's re-export test are what
    # actually police the public surface - so an *incidentally* bound submodule
    # (backend, client, policy, _http - none of them in __all__) is excluded here.
    # `exceptions` is the one submodule that IS a deliberate export (Task 8 widens
    # __all__ to include it), so the module check only excludes a module when it is
    # NOT already named in __all__ - otherwise the exclusion would silently swallow
    # the one module this package intentionally re-exports.
    public = {
        n
        for n in dir(csa_zendesk)
        if (not n.startswith("_") or n in csa_zendesk.__all__)
        and (n in csa_zendesk.__all__ or not inspect.ismodule(getattr(csa_zendesk, n)))
    }
    assert public == set(csa_zendesk.__all__)


def test_the_types_an_embedder_needs_are_exported():
    # Anyone writing a custom Backend or policy needs these by name.
    for name in (
        "Backend",
        "Envelope",
        "FakeBackend",
        "ApiBackend",
        "HttpClient",
        "Policy",
        "PolicyBackend",
        "ZendeskClient",
        "exceptions",
        "PROFILES",
        "ALL_CAPABILITIES",
    ):
        assert name in csa_zendesk.__all__, name


def test_nothing_writes_to_stdout_when_any_module_is_imported_fresh():
    # Under stdio MCP, stdout IS the JSON-RPC channel: one stray byte AT IMPORT
    # TIME corrupts the session before a single tool call happens, and the server
    # looks alive while answering nothing.
    #
    # `importlib.reload(csa_zendesk)` re-executes only __init__.py - every module
    # it imports is already cached in sys.modules and is NOT re-executed, so a
    # print() at module scope in any of the other eight modules left an earlier
    # version of this test green even though a real cold start (the only import a
    # freshly launched server ever does) would have printed it. Genuinely removing
    # every module of this package from sys.modules before each import is what
    # makes the test see what a cold start sees, one module at a time so a failure
    # names which module misbehaved.
    #
    # This is an IMPORT-TIME guard ONLY. A print() called from inside a function
    # is not exercised here at all - it only runs, and would only be caught, when
    # that function actually executes. Verified live: a print() at module scope
    # in _http.py makes this test fail; a print() inside one of _http.py's
    # functions does not, because nothing here ever calls that function.
    import contextlib
    import importlib
    import io
    import pkgutil
    import sys

    # Discovered from the files on disk, not from any list this package
    # maintains about itself - an import-time guard that only checked modules
    # the package already claims to have would not be independent of the thing
    # it is guarding.
    #
    # `walk_packages`, not `iter_modules`: `iter_modules` does not recurse, so
    # it sees a subpackage like `auth` as a single opaque entry and never looks
    # inside it - `auth._store`, `auth._pkce`, `auth._callback` and whatever
    # `auth` gains next would all sit outside this guard entirely, unchecked,
    # while the test kept passing. That is exactly the failure mode the
    # comment below warns about, and it is the likeliest place for it to bite:
    # the auth package is where a browser prompt and a paste-fallback prompt
    # live - the two things in this codebase most likely to reach for print().
    # `walk_packages` already yields fully-qualified names when given `prefix`,
    # so they are used as-is rather than prefixed again.
    module_names = sorted(info.name for info in pkgutil.walk_packages(csa_zendesk.__path__, prefix="csa_zendesk."))
    module_names.append("csa_zendesk")  # the package's own __init__.py
    # If this count ever changes, a module was added or removed - update the
    # number, but do not delete the assertion: without it, a module quietly
    # excluded from the loop below would leave this guard passing while
    # covering less than it claims to.
    assert len(module_names) == 20, f"expected 20 modules, found {module_names}"

    for name in module_names:
        for cached in [n for n in sys.modules if n == "csa_zendesk" or n.startswith("csa_zendesk.")]:
            del sys.modules[cached]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            importlib.import_module(name)
        assert buf.getvalue() == "", f"{name} wrote to stdout on import: {buf.getvalue()!r}"
