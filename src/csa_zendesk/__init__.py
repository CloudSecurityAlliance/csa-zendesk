"""A Python library and local stdio MCP server over the Zendesk REST API.

Block 0 is a vertical slice: HTTP, the error taxonomy, pagination, the Backend
seam, the fail-closed policy wrapper and the client, proven on `get_ticket`.
Nothing here touches MCP; that starts in Block 1.

Deliberately NOT `from __future__ import annotations`: every future import binds
a name (`annotations`) in the importing module's own namespace, and this is the
one module whose namespace IS the public surface - `from __future__ import
annotations` here would put `annotations` in `dir(csa_zendesk)`, which is not a
name this package exports and not one `__all__` lists. This file has no
annotations of its own that need it deferred.
"""

from . import exceptions
from ._http import HttpClient
from .backend import ApiBackend, Backend, Envelope, FakeBackend
from .client import ZendeskClient
from .policy import ALL_CAPABILITIES, PROFILES, Policy, PolicyBackend

__version__ = "0.0.1"

__all__ = [
    "ALL_CAPABILITIES",
    "ApiBackend",
    "Backend",
    "Envelope",
    "FakeBackend",
    "HttpClient",
    "PROFILES",
    "Policy",
    "PolicyBackend",
    "ZendeskClient",
    "__version__",
    "exceptions",
]
