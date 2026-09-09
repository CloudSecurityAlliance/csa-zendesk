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
    # actually police the public surface - so submodules are excluded here.
    public = {
        n
        for n in dir(csa_zendesk)
        if (not n.startswith("_") or n in csa_zendesk.__all__) and not inspect.ismodule(getattr(csa_zendesk, n))
    }
    assert public == set(csa_zendesk.__all__)
