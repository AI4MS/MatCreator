"""Registry mapping remote-job provider names to adapter instances.

Adding a new provider means adding one adapter module implementing
``RemoteJobAdapter`` and one ``register_adapter`` call in
``providers/__init__.py`` — nothing else in the control plane changes.
"""
from __future__ import annotations

from typing import Callable

from .base import RemoteJobAdapter

_FACTORIES: dict[str, Callable[[], RemoteJobAdapter]] = {}
_INSTANCES: dict[str, RemoteJobAdapter] = {}


def register_adapter(provider: str, factory: Callable[[], RemoteJobAdapter]) -> None:
    """Register a lazy factory for one provider's adapter.

    Factories are not called until first use, so importing the registry never
    imports an optional provider SDK or shells out to a CLI.
    """
    if not provider:
        raise ValueError("provider is required")
    _FACTORIES[provider] = factory
    _INSTANCES.pop(provider, None)


def get_adapter(provider: str) -> RemoteJobAdapter:
    """Return the (lazily constructed, cached) adapter for ``provider``."""
    if provider not in _FACTORIES:
        raise KeyError(f"No remote-job adapter is registered for provider '{provider}'")
    if provider not in _INSTANCES:
        adapter = _FACTORIES[provider]()
        if adapter.provider != provider:
            raise ValueError(
                f"Adapter registered for '{provider}' reports provider '{adapter.provider}'"
            )
        _INSTANCES[provider] = adapter
    return _INSTANCES[provider]


def registered_providers() -> list[str]:
    return sorted(_FACTORIES)


def reset_registry() -> None:
    """Test helper: drop all registrations and cached adapter instances."""
    _FACTORIES.clear()
    _INSTANCES.clear()
