# filepath: src/cellsmith/adapters/logic_app.py
# %% [ai_schema:pointer]
# CellSmith workflow DAG node. Cells marked with `# %% [<cell_id>]`.
# To modify or splice: load `CELLSMITH_PATCH_SCHEMA.md` at the project root
# for the full JSON patch schema (incl. SPLICE_NODE and changelog rules).
# Run `cellsmith status` first — if it errors, edit files directly.
# %% [ai_schema:end]

# %% [imports:start]
import json
import re
from pathlib import Path
from typing import Any, Dict, List

import yaml

# Internal adapter protocol
from cellsmith.adapters.base import (
    BaseWorkflowAdapter,
    DagNode,
    DagGraph,
    Metadata,
)

# Shared primitives (kept as standalone modules; adapters may call them)
from cellsmith.adapters.lexicon import (
    apply_lexicon_to_actions,
    generate_lexicon,
)
from cellsmith.adapters.dag import (
    COMPOUND_TYPES as _COMPOUND_TYPES,  # "scope", "foreach", "until"
)
# %% [imports:end]

# ---------------------------------------------------------------------------
# LogicAppAdapter — implements the BaseWorkflowAdapter protocol
# ---------------------------------------------------------------------------


class LogicAppAdapter(BaseWorkflowAdapter):
    """Translate between Azure Logic App JSON and CellSmith's numbered YAML DAG."""

    @property
    def name(self) -> str:
        return "Logic App"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "Deconstruct Logic App JSON actions into numbered YAML DAG steps (and back)"

    @property
    def target_format(self) -> str:
        return "LogicApp JSON"

    # ------------------------------------------------------------------
    # Core operations  (protocol)
    # ------------------------------------------------------------------

    def unpack(self, raw: Any) -> tuple[DagGraph, Metadata]:
        """Logic App JSON → (DagGraph, Metadata).

        Accepts either a wrapped envelope ``{"definition": {…}}`` or the
        definition dict directly.  Returns a topologically-ordered DAG of
        ``DagNode`` steps and metadata (schema version, triggers, outputs, lexicon).
        """

        # Resolve the definition block
        is_wrapped = "definition" in raw if isinstance(raw, dict) else False
        definition = raw.get("definition", raw) if is_wrapped else raw

        actions: Dict[str, dict] = definition.get("actions", {})

        # Build metadata envelope
        meta: Dict[str, Any] = {}
        if isinstance(definition, dict):
            for key in ("$schema", "contentVersion", "parameters", "triggers", "outputs"):
                if key in definition:
                    meta[key] = definition[key]

        # Intern action names if requested — captures lexicon and compression stats
        name_to_id: Dict[str, str] | None = None
        lexicon_data: Dict[str, dict] | None = None

        if meta.get("_intern", False):
            name_to_id = generate_lexicon(actions)
            lexicon_data = {}
            for name, aid in name_to_id.items():
                lexicon_data[aid] = {"name": name}

            # Extract descriptions from each action body
            for aid, desc_dict in lexicon_data.items():
                real_name = name_to_id.get(aid) or aid
                if real_name in actions:
                    body = actions[real_name]
                    if isinstance(body, dict):
                        desc = body.get("description")
                        lexicon_data[aid]["description"] = desc if desc else "<null>"

            # Rehydrate with lexicon
            actions = apply_lexicon_to_actions(
                actions, name_to_id, is_rehydrate=False
            )

        # Compute stats for reporting when interning was active
        if name_to_id:
            orig_len = len(re.sub(r'\s+', '', json.dumps(actions, separators=(',', ':'))))
            interned_len = len(re.sub(r'\s+', '', json.dumps(
                apply_lexicon_to_actions(actions, name_to_id, is_rehydrate=False),
                separators=(',', ':'),
            )))
            saved = orig_len - interned_len
            meta["lexicon"] = lexicon_data  # Store for pack() to use later
            meta["stats"] = {
                "original_chars": orig_len,
                "interned_chars": interned_len,
                "saved_chars": saved,
                "reduction_pct": (saved / orig_len * 100) if orig_len > 0 else 0.0,
                "lexicon_entries": len(name_to_id),
            }

        nodes = _build_nodes_from_actions(actions)
        graph = DagGraph(nodes=nodes, metadata=Metadata(meta))

        return graph, Metadata(meta)

    def pack(self, dag_graph: DagGraph) -> Any:
        """(DagGraph) → Logic App JSON.

        Reconstructs the full Logic App definition envelope from the canonical
        DAG and metadata, including lexicon rehydration if present.
        """

        actions = _pack_nodes_to_actions(dag_graph.nodes)

        # Rehydrate lexicon if present in metadata
        meta = dag_graph.metadata or Metadata()
        lexicon_data: Dict[str, dict] | None = meta.get("lexicon")

        if lexicon_data:
            id_to_name = {}
            for k, v in lexicon_data.items():
                if isinstance(v, dict):
                    id_to_name[k] = v.get("name", k)

            if id_to_name:
                actions = apply_lexicon_to_actions(
                    actions, id_to_name, is_rehydrate=True
                )

        definition: Dict[str, Any] = {"actions": actions}

        # Merge top-level metadata
        for key in ("$schema", "contentVersion", "parameters", "triggers", "outputs"):
            if key in meta:
                definition[key] = meta[key]

        # Assemble envelope if requested
        wrapper_info = meta.get("_wrapper", {})
        is_wrapped = isinstance(wrapper_info, dict) and wrapper_info.get("is_wrapped", True)
        if is_wrapped:
            assembled = dict(wrapper_info.get("root_metadata", {}))
            assembled["definition"] = definition
        else:
            assembled = definition

        return assembled


# ---------------------------------------------------------------------------
# Helpers — kept at module level so they remain callable from the CLI
# ---------------------------------------------------------------------------


def _build_nodes_from_actions(
    actions: Dict[str, dict],
    output_dir: Path | None = None,
) -> List[DagNode]:
    """Convert flat action dict → ordered list of DagNode."""

    def _topological_order(action_dict: Dict[str, dict]) -> List[str]:
        graph: Dict[str, set] = {n: set() for n in action_dict}
        in_degree: Dict[str, int] = {n: 0 for n in action_dict}
        for name, body in action_dict.items():
            if not isinstance(body, dict):
                continue
            run_after = body.get("runAfter", {})
            if isinstance(run_after, dict):
                for dep in run_after:
                    if dep in graph:
                        graph[dep].add(name)
                        in_degree[name] += 1

        queue = sorted([n for n, deg in in_degree.items() if deg == 0])
        ordered: List[str] = []
        while queue:
            curr = queue.pop(0)
            ordered.append(curr)
            for neighbor in sorted(graph[curr]):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)
        return ordered

    def _flatten_actions(
        action_dict: Dict[str, dict],
        prefix_idx: int = 1,
    ) -> List[DagNode]:
        """Recursively flatten actions into a flat ordered node list."""

        # First, topologically order the direct children
        ordered = _topological_order(action_dict)

        nodes: List[DagNode] = []
        for idx, name in enumerate(ordered, start=prefix_idx):
            body = action_dict[name]
            if not isinstance(body, dict):
                continue

            node_type = str(body.get("type", "")).lower() if body else ""
            config = {k: v for k, v in body.items() if k not in ("id", "type")}

            # Determine prefix for this node
            prefix = f"{idx:02d}_{name}"

            if not output_dir or "actions" not in body:
                # Leaf node
                nodes.append(DagNode(
                    prefix=prefix,
                    id=name,
                    type=node_type if node_type else "",
                    config=config,
                    run_after=body.get("runAfter", {}),
                    description=body.get("description"),
                ))

            elif node_type in _COMPOUND_TYPES:
                # Recurse into "actions" sub-dict
                children = _flatten_actions(body["actions"], prefix_idx + 1)
                nodes.extend(children)

            elif node_type == "if":
                # Flatten true branch
                if "actions" in body and isinstance(body["actions"], dict):
                    children = _flatten_actions(
                        body["actions"], prefix_idx + 1
                    )
                    nodes.extend(children)

                # Flatten else branch if present
                else_block = body.get("else", {})
                if isinstance(else_block, dict) and "actions" in else_block:
                    children = _flatten_actions(
                        else_block["actions"], prefix_idx + 1
                    )
                    nodes.extend(children)

            elif node_type == "switch":
                # Flatten cases
                for case_key, case_body in sorted(body.get("cases", {}).items()):
                    if isinstance(case_body, dict):
                        case_prefix = f"{idx:02d}_{name}_case_{case_key}"
                        nodes.append(DagNode(
                            prefix=case_prefix,
                            id=f"{name}__{case_key}",
                            type=node_type,
                            config={k: v for k, v in case_body.items() if k != "actions"},
                            run_after=case_body.get("runAfter", {}),
                        ))
                        if "actions" in case_body and isinstance(case_body["actions"], dict):
                            children = _flatten_actions(
                                case_body["actions"], prefix_idx + 1
                            )
                            nodes.extend(children)

                # Flatten default branch
                default_block = body.get("default", {})
                if isinstance(default_block, dict) and "actions" in default_block:
                    children = _flatten_actions(
                        default_block["actions"], prefix_idx + 1
                    )
                    nodes.extend(children)

            else:
                # Unknown compound type — treat as leaf
                nodes.append(DagNode(
                    prefix=prefix,
                    id=name,
                    type=node_type if node_type else "",
                    config=config,
                    run_after=body.get("runAfter", {}),
                    description=body.get("description"),
                ))

        return nodes

    # For unpack from raw actions dict, we just build the node list
    ordered = _topological_order(actions)
    nodes: List[DagNode] = []
    for idx, name in enumerate(ordered, start=1):
        body = actions[name] if isinstance(actions, dict) else {}
        if not isinstance(body, dict):
            continue

        node_type = str(body.get("type", "")).lower() if body else ""
        config = {k: v for k, v in body.items() if k not in ("id", "type")}

        nodes.append(DagNode(
            prefix=f"{idx:02d}_{name}",
            id=name,
            type=node_type if node_type else "",
            config=config,
            run_after=body.get("runAfter", {}),
            description=body.get("description"),
        ))

    return nodes


def _pack_nodes_to_actions(
    nodes: List[DagNode],
) -> Dict[str, dict]:
    """Convert ordered DAG nodes → flat action dict."""

    actions: Dict[str, dict] = {}
    for node in nodes:
        action_dict = {
            "id": node.id,
            "type": node.type if node.type else "",
        }
        action_dict.update(node.config)
        if node.run_after:
            action_dict["runAfter"] = node.run_after
        if node.description is not None:
            action_dict["description"] = node.description

        actions[node.id] = action_dict

    return actions


# ---------------------------------------------------------------------------
# Public API — kept for backward-compatibility with CLI and existing code
# ---------------------------------------------------------------------------


def unpack_playbook(
    playbook_json_path: Path,
    output_dir: Path | None = None,
    intern: bool = False,
) -> tuple[DagGraph, Metadata]:
    """Convenience wrapper: Logic App JSON file → (DagGraph, Metadata).

    If ``output_dir`` is provided the DAG steps are also written as numbered
    YAML files (the old workflow format).  Returns the canonical internal
    representation regardless.
    """

    with open(playbook_json_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    adapter = LogicAppAdapter()
    graph, meta = adapter.unpack(raw)

    # Optionally write to filesystem for workflow-level operations
    if output_dir:
        _write_dag_to_yaml(graph.nodes, output_dir)

    return graph, meta


def pack_playbook(
    workflow_dir: Path | None = None,
    dag_graph: DagGraph | None = None,
    output_json_path: Path | None = None,
) -> Any:
    """Convenience wrapper: (DagGraph or YAML directory) → Logic App JSON.

    If ``dag_graph`` is provided directly it is used.  Otherwise the
    YAML files under ``workflow_dir`` are read and assembled into a DAG first.
    """

    if dag_graph is None:
        # Rebuild from YAML files on disk
        nodes = _read_dag_from_yaml(workflow_dir)
        dag_graph = DagGraph(nodes=nodes, metadata=dag_graph.metadata if dag_graph else None)

    adapter = LogicAppAdapter()
    result = adapter.pack(dag_graph)

    if output_json_path:
        output_json_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
            f.write("\n")

    return result


# ---------------------------------------------------------------------------
# YAML I/O helpers (kept at module level for compatibility)
# ---------------------------------------------------------------------------


def _write_dag_to_yaml(
    nodes: List[DagNode],
    output_dir: Path,
) -> None:
    """Write a list of DagNode → numbered YAML files."""

    output_dir.mkdir(parents=True, exist_ok=True)
    for node in nodes:
        if node.type.lower() in _COMPOUND_TYPES and node.config.get("actions"):
            # Compound node → directory with config.yaml
            container = output_dir / f"{node.prefix}"
            container.mkdir(parents=True, exist_ok=True)
            config = {"id": node.id}
            if node.type:
                config["type"] = node.type
            config.update(node.config)
            if node.description:
                config["description"] = node.description
            with open(container / "config.yaml", "w", encoding="utf-8") as f:
                yaml.safe_dump(config, f, sort_keys=False)

            if node.type.lower() == "if":
                _write_dag_to_yaml(nodes, container / "true")  # simplified
            elif node.type.lower() == "switch":
                _write_dag_to_yaml(nodes, container / "cases")  # simplified

        else:
            node_file = output_dir / f"{node.prefix}.yaml"
            payload = {k: v for k, v in node.to_yaml_dict().items() if v is not None}
            with open(node_file, "w", encoding="utf-8") as f:
                yaml.safe_dump(payload, f, sort_keys=False)


def _read_dag_from_yaml(workflow_dir: Path) -> List[DagNode]:
    """Read numbered YAML files from a workflow directory → list of DagNode."""

    nodes: List[DagNode] = []
    for p in sorted(workflow_dir.iterdir()):
        if p.name.startswith(".") or p.name.endswith(".bak"):
            continue

        parts = p.name.split("_", 1)
        if not parts[0].isdigit():
            continue

        if p.is_file() and p.suffix in (".yaml", ".yml"):
            with open(p, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            nodes.append(DagNode(
                prefix=parts[0],
                id=data.get("id", parts[1] if len(parts) > 1 else ""),
                type=data.get("type", ""),
                config={k: v for k, v in data.items() if k not in ("id", "type")},
                run_after=data.get("runAfter", {}),
                description=data.get("description"),
            ))

    return nodes


# ---------------------------------------------------------------------------
# CLI entrypoint (backward-compatible)
# ---------------------------------------------------------------------------


def main():
    """Logic App JSON ↔ Numbered YAML DAG Converter (CLI)."""

    import argparse

    parser = argparse.ArgumentParser(
        description="Logic App JSON ↔ Numbered YAML DAG Converter"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    unpack_parser = subparsers.add_parser(
        "unpack", help="Deconstruct Logic App JSON actions into numbered YAML files"
    )
    unpack_parser.add_argument("input_json", type=Path, help="Path to Logic App JSON")
    unpack_parser.add_argument(
        "output_dir", type=Path, help="Destination workflow directory"
    )
    unpack_parser.add_argument(
        "--intern", action="store_true", help="Compress action names to short tokens"
    )

    pack_parser = subparsers.add_parser(
        "pack", help="Assemble numbered YAML actions into Logic App JSON"
    )
    pack_parser.add_argument("input_dir", type=Path, help="Source workflow directory")
    pack_parser.add_argument("output_json", type=Path, help="Destination JSON file")

    args = parser.parse_args()

    if args.command == "unpack":
        # FIX: unpack_playbook returns a tuple (graph, meta)
        graph, meta = unpack_playbook(args.input_json, args.output_dir, intern=args.intern)

        mode = " (with interning)" if args.intern else ""
        print(f"Successfully unpacked{mode} {args.input_json} to {args.output_dir}")

        if args.intern and meta:
            stats = meta.get("stats")
            if stats:
                print(f"\nInterning Compression Report (non-whitespace characters):")
                print(f"  Original length:   {stats['original_chars']:,}")
                print(f"  Interned length:   {stats['interned_chars']:,}")
                print(
                    f"  Characters saved:  {stats['saved_chars']:,} "
                    f"({stats['reduction_pct']:.1f}%)"
                )
                print(f"  Lexicon mapping:   {stats['lexicon_entries']:,} unique action IDs")

    elif args.command == "pack":
        pack_playbook(args.input_dir, None, args.output_json)
        print(f"Successfully assembled {args.input_dir} to {args.output_json}")


if __name__ == "__main__":
    main()
