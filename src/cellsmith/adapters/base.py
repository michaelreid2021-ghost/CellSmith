# filepath: src/cellsmith/adapters/base.py
"""Canonical adapter protocol for CellSmith workflow translation.

Every adapter implements two operations:

  unpack(raw)   →  (DagGraph, Metadata)
  pack(dag_graph)  →  raw

The adapter is language/protocol-agnostic — it doesn't know about Logic Apps,
Airflow DAGs, or Azure Logic Apps. It only knows the normalized internal
representation (a directed acyclic graph of numbered YAML steps) and one
external wire format.

External format (raw): any serializable structure (dict, JSON, YAML, …)
Internal representation: DagGraph (ordered DAG of step dictionaries with runAfter edges) + Metadata

The contract does not depend on `dag.py` or `lexicon.py`, but those modules
are provided as shared utilities that adapters may call internally when
building or consuming the internal representation.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Internal Representation (shared across all adapters)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, order=False)
class DagNode:
    """A single step in the workflow DAG.

    Attributes
    ----------
    prefix : str
        Zero-padded ordinal (e.g. "01", "02").  Determined by the adapter
        during *pack*; used for filesystem ordering.
    id : str
        Human-readable / semantic identifier (e.g. "fetch_user_data").
    type : str
        Action classification used by the adapter (e.g. "Scope", "If", "Action").
    config : dict[str, Any]
        Free-form configuration keys for this step.  Keys with reserved names
        ("id", "type") are excluded.
    run_after : dict[str, List[str]]
        Dependency edges: { predecessor_id: [list_of_acceptable_statuses] }.
    description : str | None
        Optional free-text description.  Populated during unpack if the
        source provides one; may be <null> as a placeholder.
    """

    prefix: str
    id: str
    type: str = ""
    config: Dict[str, Any] = field(default_factory=dict)
    run_after: Dict[str, List[str]] = field(default_factory=dict)
    description: str | None = None

    def to_yaml_dict(self) -> Dict[str, Any]:
        """Return a dict suitable for yaml.dump()."""
        out = {"id": self.id, "type": self.type} if self.type else {"id": self.id}
        out.update({k: v for k, v in self.config.items() if v is not None})
        if self.run_after:
            out["runAfter"] = self.run_after
        if self.description is not None:
            out["description"] = self.description
        return out


class Metadata(Mapping[str, Any]):
    """Key-value metadata accompanying a DagGraph.

    This is an opaque bag — adapters fill it with whatever context they need
    (lexicon mapping, schema version, triggers, outputs, etc.).  The only
    requirement is that it be reversible through ``pack()``.
    """

    def __init__(self, data: Mapping[str, Any] | None = None):
        self._data = dict(data or {})

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._data[key] = value

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def __iter__(self):
        return iter(self._data)

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self._data)


@dataclass(frozen=True, order=False)
class DagGraph:
    """Ordered DAG of workflow steps.

    The adapter is responsible for ensuring the node list is topologically
    sorted (no cycles).  This graph is the *sole canonical representation*
    of a workflow between unpack and pack.

    Parameters
    ----------
    nodes : list[DagNode]
        Steps ordered by dependency order.  Each node's ``run_after`` dicts
        must reference only IDs present in the graph.
    metadata : Metadata | None
        Ancillary context (lexicon, schema version, triggers, outputs, …).
    """

    nodes: List[DagNode] = field(default_factory=list)
    metadata: Metadata | None = field(default=None)

    def __post_init__(self):
        # Basic invariant checks — adapters should enforce these too.
        ids = {n.id for n in self.nodes}
        for node in self.nodes:
            if node.run_after:
                for dep in node.run_after:
                    assert dep in ids, (
                        f"Node '{node.id}' depends on unknown ID '{dep}'."
                    )


# ---------------------------------------------------------------------------
# Adapter Protocol
# ---------------------------------------------------------------------------

class BaseWorkflowAdapter(ABC):
    """Protocol that every CellSmith workflow adapter must satisfy.

    Subclasses implement ``unpack`` and ``pack``, then register themselves
    via Python entry points under the group name ``cellsmith.adapters``.

    Example registration in ``pyproject.toml::

        [project.entry-points."cellsmith.adapters"]
        "logic_app" = "cellsmith.adapters.logic_app:LogicAppAdapter"

    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable identifier shown in ``cellsmith adaptors list``."""

    @property
    @abstractmethod
    def version(self) -> str:
        """Semantic version (e.g. ``"0.1.0"``)."""

    @property
    @abstractmethod
    def description(self) -> str:
        """One-line summary of what the adapter translates between."""

    @property
    @abstractmethod
    def target_format(self) -> str:
        """Canonical name of the external format (e.g. ``"LogicApp JSON"``)."""

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    @abstractmethod
    def unpack(self, raw: Any) -> tuple[DagGraph, Metadata]:
        """Ingest an external workflow representation and return the internal DAG.

        Parameters
        ----------
        raw : Any
            Source data in the adapter's native format.  The concrete type
            depends on the adapter (typically a dict deserialized from JSON or
            YAML).

        Returns
        -------
        (DagGraph, Metadata)
            A topologically-ordered DAG of ``DagNode`` steps and any ancillary
            metadata (lexicon, schema version, triggers, outputs).

        Raises
        ------
        ValueError
            If the input is malformed or contains unresolvable dependencies.
        """

    @abstractmethod
    def pack(self, dag_graph: DagGraph) -> Any:
        """Reconstruct the external workflow representation from the internal DAG.

        Parameters
        ----------
        dag_graph : DagGraph
            The canonical internal representation produced by ``unpack()``.
            Metadata is stored inside the graph (``dag_graph.metadata``).

        Returns
        -------
        Any
            Reconstructed data in the adapter's native format (typically a dict).

        Raises
        ------
        ValueError
            If the DAG is invalid (cycles, missing dependencies).
        """
