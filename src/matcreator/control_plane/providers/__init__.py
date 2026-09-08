"""Built-in remote-job provider adapters.

Importing this package registers every built-in adapter. Adding a new
provider means adding one adapter module and one ``register_adapter`` call
below — nothing else in the control plane needs to change.
"""
from __future__ import annotations

from .base import (
    CapabilityError,
    RemoteJobAdapter,
    RemoteJobCapability,
    RemoteJobStatus,
    RemoteJobSubmissionUncertainError,
)
from .registry import get_adapter, register_adapter, registered_providers, reset_registry


def _register_builtin_adapters() -> None:
    # Registered as lazy factories (not imported eagerly) so importing this
    # package never pays for an adapter's own imports (SDK modules, env-var
    # setup) until `get_adapter(...)` actually constructs one.
    def _e2b_factory():
        from .e2b import E2BSandboxAdapter

        return E2BSandboxAdapter()

    def _bohr_sandbox_factory():
        from .bohr_sandbox import BohrSandboxAdapter

        return BohrSandboxAdapter()

    def _bohr_batchjob_factory():
        from .bohr_batchjob import BohrBatchJobAdapter

        return BohrBatchJobAdapter()

    register_adapter("e2b", _e2b_factory)
    register_adapter("bohr_sandbox", _bohr_sandbox_factory)
    register_adapter("bohr_batchjob", _bohr_batchjob_factory)


_register_builtin_adapters()

__all__ = [
    "RemoteJobAdapter",
    "RemoteJobCapability",
    "RemoteJobStatus",
    "RemoteJobSubmissionUncertainError",
    "CapabilityError",
    "get_adapter",
    "register_adapter",
    "registered_providers",
    "reset_registry",
]
