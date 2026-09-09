import inspect

import csa_zendesk


def test_version_is_a_dotted_string():
    assert isinstance(csa_zendesk.__version__, str)
    assert csa_zendesk.__version__.count(".") >= 2


def test_package_exports_nothing_it_has_not_declared() -> None:
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


def test_nothing_writes_to_stdout_on_import():
    # Under stdio MCP, stdout IS the JSON-RPC channel. One stray byte corrupts the
    # session and the server looks alive while answering nothing.
    import contextlib
    import importlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        importlib.reload(csa_zendesk)
    assert buf.getvalue() == ""
