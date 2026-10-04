# filepath: src/cellsmith/registry.py
"""Runtime adapter registry — discovers and loads adapters via importlib.metadata.

Every adapter must register itself under the entry-point group name
``cellsmith.adapters``:

    [project.entry-points."cellsmith.adapters"]
        logic_app = "cellsmith.adapters.logic_app:LogicAppAdapter"

The registry collects all registered adapters and provides a simple lookup API.
It never writes to disk or manages external packages — it only *discovers*
already-installed adapters.

"""

from __future__ import annotations

import logging
from typing import Dict, List

from cellsmith.adapters.base import BaseWorkflowAdapter

logger = logging.getLogger(__name__)


class AdaptorRegistry:
    """Thread-safe registry of discovered workflow adapters.

    Usage
    -----
    >>> from cellsmith.registry import load_all_adapters, get_adapter
    >>> adapters = load_all_adapters()       # returns list[BaseWorkflowAdapter]
    >>> adapter = get_adapter("logic_app")     # lookup by canonical slug
    """

    _instance: AdaptorRegistry | None = None
    _initialized: bool = False

    def __init__(self) -> None:
        if AdaptorRegistry._instance is not None:
            return

        self._adaptors: Dict[str, BaseWorkflowAdapter] = {}
        AdaptorRegistry._instance = self

    def discover(self) -> List[BaseWorkflowAdapter]:
        """Scan entry points and lazy-instantiate all registered adapters.

        Returns
        -------
        list[BaseWorkflowAdapter]
            List of adapter instances (order is insertion order).
        """

        if AdaptorRegistry._initialized:
            return list(self._adaptors.values())

        import importlib.metadata  # noqa: F401 — used below

        for ep in importlib.metadata.entry_points(group="cellsmith.adapters"):
            try:
                adapter_cls = ep.load()
                instance: BaseWorkflowAdapter = adapter_cls()

                # Use the canonical entry-point name (e.g. "logic_app") as key,
                # but store the human-readable instance.name for display
                ep_key = ep.name

                if ep_key in self._adaptors:
                    logger.warning(
                        "Adapter '%s' is registered twice (by %s and %s). Keeping first.",
                        ep_key,
                        AdaptorRegistry._adaptors[ep_key].name,
                        instance.name,
                    )
                else:
                    self._adaptors[ep_key] = instance

            except Exception as exc:  # noqa: BLE001 — entry points can fail silently
                logger.warning("Failed to load adapter '%s': %s", ep.name, exc)

        AdaptorRegistry._initialized = True
        return list(self._adaptors.values())

    def get(self, key: str) -> BaseWorkflowAdapter | None:
        """Retrieve an adapter by its canonical entry-point key."""
        return self._adaptors.get(key)

    def list_names(self) -> List[str]:
        """Return all registered adapter canonical keys."""
        return list(self._adaptors.keys())


# ---------------------------------------------------------------------------
# Public convenience functions
# ---------------------------------------------------------------------------

def load_all_adapters() -> List[BaseWorkflowAdapter]:
    """Return every registered adapter as a list of instances.

    Uses the module-level singleton to avoid per-call discovery overhead.
    """
    return AdaptorRegistry().discover()


def get_adapter(key: str) -> BaseWorkflowAdapter | None:
    """Return a single adapter by its canonical entry-point key."""
    return AdaptorRegistry().get(key)
