# filepath: src/cellsmith/adapters/dag.py
# %% [ai_schema:pointer]
# CellSmith-annotated file. Cells marked with `# %% [<cell_id>]`.
# To modify safely: load `CELLSMITH_PATCH_SCHEMA.md` at the project root
# for the full JSON patch schema (incl. required `changelog` block).
# Run `cellsmith status` first — if it errors, ignore these markers and
# edit the file directly per the user's request.
# %% [ai_schema:end]

# %% [imports:start]
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
import yaml
# %% [imports:end]


# %% [class:DAGValidationError:start]
class DAGValidationError(Exception):
    """Raised when workflow dependencies contain cycles or invalid references."""
    pass
# %% [class:DAGValidationError:end]


# %% [func:parse_node_info:start]
def parse_node_info(path: Path) -> Tuple[str, dict]:
    if path.is_file() and path.suffix in (".yaml", ".yml"):
        with open(path, "r", encoding="utf-8") as f:
            parsed = yaml.safe_load(f)
        data = parsed if isinstance(parsed, dict) else {}
        node_id = data.get("id", path.stem.split("_", 1)[-1])
        return node_id, data
    elif path.is_dir():
        for cfg_name in ("config.yaml", "loop_config.yaml", "config.yml", "loop_config.yml"):
            cfg_file = path / cfg_name
            if cfg_file.exists():
                with open(cfg_file, "r", encoding="utf-8") as f:
                    parsed = yaml.safe_load(f)
                data = parsed if isinstance(parsed, dict) else {}
                node_id = data.get("id", path.name.split("_", 1)[-1])
                return node_id, data
        return path.name.split("_", 1)[-1], {}
    return path.stem, {}
# %% [func:parse_node_info:end]


# %% [func:list_ordered_nodes:start]
def list_ordered_nodes(target_dir: Path) -> List[Dict[str, any]]:
    nodes = []
    for item in target_dir.iterdir():
        if item.name.startswith(".") or item.name.endswith(".bak"):
            continue
        parts = item.name.split("_", 1)
        if parts[0].isdigit():
            node_id, content = parse_node_info(item)
            nodes.append({
                "path": item,
                "prefix": int(parts[0]),
                "raw_prefix": parts[0],
                "slug": parts[1] if len(parts) > 1 else "",
                "id": node_id,
                "content": content,
            })
    nodes.sort(key=lambda n: (n["prefix"], n["path"].name))
    return nodes
# %% [func:list_ordered_nodes:end]


# %% [func:validate_dag:start]
def validate_dag(nodes: List[Dict[str, any]]) -> None:
    seen_ids: Set[str] = set()
    for n in nodes:
        nid = n["id"]
        if nid in seen_ids:
            raise DAGValidationError(f"Duplicate node id '{nid}' detected in workflow.")
        seen_ids.add(nid)

    graph: Dict[str, Set[str]] = {n["id"]: set() for n in nodes}
    in_degree: Dict[str, int] = {n["id"]: 0 for n in nodes}

    for n in nodes:
        run_after = n["content"].get("runAfter", {})
        if isinstance(run_after, dict):
            for dep in run_after.keys():
                if dep in graph:
                    graph[dep].add(n["id"])
                    in_degree[n["id"]] += 1
                else:
                    raise DAGValidationError(f"Unknown dependency '{dep}' declared in '{n['id']}'.")

    queue = [node_id for node_id, deg in in_degree.items() if deg == 0]
    visited_count = 0

    while queue:
        curr = queue.pop(0)
        visited_count += 1
        for neighbor in graph[curr]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if visited_count != len(nodes):
        raise DAGValidationError("Cycle detected in workflow dependency graph.")
# %% [func:validate_dag:end]


# %% [func:splice_node:start]
def splice_node(
    target_dir: Path,
    after_id: Optional[str],
    new_id: str,
    node_type: str,
    raw_content: Optional[str] = None,
    statuses: Optional[List[str]] = None,
) -> Tuple[Path, List[Path]]:
    if not target_dir.exists() or not target_dir.is_dir():
        raise FileNotFoundError(f"Target directory '{target_dir}' does not exist.")

    nodes = list_ordered_nodes(target_dir)
    target_idx = -1
    predecessor_node = None

    if after_id is not None:
        for idx, n in enumerate(nodes):
            if (
                n["id"] == after_id
                or n["path"].name == after_id
                or n["path"].stem == after_id
                or n["slug"] == after_id
            ):
                target_idx = idx
                predecessor_node = n
                break
        if target_idx == -1:
            raise ValueError(f"Predecessor node '{after_id}' not found in '{target_dir}'.")

    insert_prefix = (nodes[target_idx]["prefix"] + 1) if target_idx != -1 else 1
    touched_paths: List[Path] = []

    for n in reversed(nodes[target_idx + 1:]):
        old_path: Path = n["path"]
        parts = old_path.name.split("_", 1)
        new_prefix_num = int(parts[0]) + 1
        new_prefix_str = f"{new_prefix_num:02d}"
        slug = parts[1] if len(parts) > 1 else ""
        new_name = f"{new_prefix_str}_{slug}" if slug else new_prefix_str
        new_path = old_path.parent / new_name
        old_path.rename(new_path)
        n["path"] = new_path
        n["prefix"] = new_prefix_num
        touched_paths.append(new_path)

    new_node_path = target_dir / f"{insert_prefix:02d}_{new_id}.yaml"
    node_data = {}
    if raw_content:
        parsed = yaml.safe_load(raw_content)
        node_data = parsed if isinstance(parsed, dict) else {}
    else:
        node_data = {"id": new_id, "type": node_type}

    if "id" not in node_data:
        node_data["id"] = new_id
    if "type" not in node_data:
        node_data["type"] = node_type

    if predecessor_node is not None:
        current_run_after = node_data.get("runAfter", {})
        if not current_run_after:
            node_data["runAfter"] = {predecessor_node["id"]: statuses or ["Succeeded"]}

    with open(new_node_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(node_data, f, sort_keys=False)

    subsequent_nodes = nodes[target_idx + 1:]
    if predecessor_node is not None:
        target_dep = predecessor_node["id"]
        for idx, n in enumerate(subsequent_nodes):
            cfg_path = n["path"]
            if cfg_path.is_dir():
                for name in ("config.yaml", "loop_config.yaml", "config.yml", "loop_config.yml"):
                    candidate = cfg_path / name
                    if candidate.exists():
                        cfg_path = candidate
                        break

            if cfg_path.is_file() and cfg_path.suffix in (".yaml", ".yml"):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    content = yaml.safe_load(f)
                if not isinstance(content, dict):
                    continue

                run_after = content.get("runAfter")
                if isinstance(run_after, dict) and target_dep in run_after:
                    transferred_status = run_after.pop(target_dep)
                    run_after[new_id] = transferred_status
                    content["runAfter"] = run_after
                    with open(cfg_path, "w", encoding="utf-8") as f:
                        yaml.safe_dump(content, f, sort_keys=False)
                    if cfg_path not in touched_paths:
                        touched_paths.append(cfg_path)
                elif idx == 0 and not run_after:
                    content["runAfter"] = {new_id: statuses or ["Succeeded"]}
                    with open(cfg_path, "w", encoding="utf-8") as f:
                        yaml.safe_dump(content, f, sort_keys=False)
                    if cfg_path not in touched_paths:
                        touched_paths.append(cfg_path)
    elif subsequent_nodes:
        first_subsequent = subsequent_nodes[0]
        cfg_path = first_subsequent["path"]
        if cfg_path.is_dir():
            for name in ("config.yaml", "loop_config.yaml", "config.yml", "loop_config.yml"):
                candidate = cfg_path / name
                if candidate.exists():
                    cfg_path = candidate
                    break
        if cfg_path.is_file() and cfg_path.suffix in (".yaml", ".yml"):
            with open(cfg_path, "r", encoding="utf-8") as f:
                content = yaml.safe_load(f)
            if isinstance(content, dict) and not content.get("runAfter"):
                content["runAfter"] = {new_id: statuses or ["Succeeded"]}
                with open(cfg_path, "w", encoding="utf-8") as f:
                    yaml.safe_dump(content, f, sort_keys=False)
                if cfg_path not in touched_paths:
                    touched_paths.append(cfg_path)

    refreshed_nodes = list_ordered_nodes(target_dir)
    validate_dag(refreshed_nodes)

    return new_node_path, touched_paths
# %% [func:splice_node:end]

# %% [func:unsplice_node:start]
def unsplice_node(
    target_dir: Path,
    new_id: str,
    after_id: Optional[str] = None,
) -> None:
    if not target_dir.exists() or not target_dir.is_dir():
        return

    nodes = list_ordered_nodes(target_dir)
    target_idx = -1
    target_node = None

    for idx, n in enumerate(nodes):
        if n["id"] == new_id or n["slug"] == new_id or n["path"].stem.endswith(f"_{new_id}"):
            target_idx = idx
            target_node = n
            break

    if target_idx == -1 or target_node is None:
        return

    spliced_path: Path = target_node["path"]
    if spliced_path.is_file():
        spliced_path.unlink()
    elif spliced_path.is_dir():
        shutil.rmtree(spliced_path)

    for n in nodes[target_idx + 1:]:
        old_path: Path = n["path"]
        parts = old_path.name.split("_", 1)
        new_prefix_num = max(1, int(parts[0]) - 1)
        new_prefix_str = f"{new_prefix_num:02d}"
        slug = parts[1] if len(parts) > 1 else ""
        new_name = f"{new_prefix_str}_{slug}" if slug else new_prefix_str
        new_path = old_path.parent / new_name
        old_path.rename(new_path)
        n["path"] = new_path
        n["prefix"] = new_prefix_num

        cfg_path = new_path
        if cfg_path.is_dir():
            for name in ("config.yaml", "loop_config.yaml", "config.yml", "loop_config.yml"):
                candidate = cfg_path / name
                if candidate.exists():
                    cfg_path = candidate
                    break

        if cfg_path.is_file() and cfg_path.suffix in (".yaml", ".yml"):
            with open(cfg_path, "r", encoding="utf-8") as f:
                content = yaml.safe_load(f)
            if isinstance(content, dict):
                run_after = content.get("runAfter")
                if isinstance(run_after, dict) and new_id in run_after:
                    transferred_status = run_after.pop(new_id)
                    if after_id:
                        run_after[after_id] = transferred_status
                    content["runAfter"] = run_after
                    with open(cfg_path, "w", encoding="utf-8") as f:
                        yaml.safe_dump(content, f, sort_keys=False)
# %% [func:unsplice_node:end]

T_FENCE = chr(39) * 3

NODE_POINTER_HEADER = (
    '# %% [ai_schema:pointer]\n'
    '# CellSmith workflow DAG node. Cells marked with `# %% [<cell_id>]`.\n'
    '# To modify or splice: load `CELLSMITH_PATCH_SCHEMA.md` at the project root\n'
    '# for the workflow DAG patch schema (incl. SPLICE_NODE and changelog rules).\n'
    '# Run `cellsmith status` first — if it errors, edit files directly.\n'
    '# %% [ai_schema:end]\n'
)

NODE_SKILL_DOC_MARKDOWN = (
    '# CellSmith Workflow DAG Patch Schema\n\n'
    'This project workflow steps are stored as sequentially numbered .yaml files and directories '    '(e.g. 01_trigger.yaml, 02_action.yaml, 03_scope/).\n'    'Files have been annotated by CellSmith with cell markers.\n\n'    '## Step 1 — probe before patching\n\n'    'Before generating any patch payload, run:\n\n'    + T_FENCE + 'bash\n'    'cellsmith status\n'    + T_FENCE + '\n\n'    '- If it prints available: generate a JSON patch per the schema below and save it as patch.json. '    'Apply with: cellsmith patch patch.json .\n'    '- If it errors: edit the target files directly.\n\n'    '## Step 2 — JSON patch shape\n\n'    + T_FENCE + 'json\n'    '{\n'    '  "revisions": [\n'    '    {\n'    '      "filename": "path/to/workflow_dir",\n'    '      "revision_type": "SPLICE_NODE",\n'    '      "after_id": "01_trigger",\n'    '      "new_id": "enrich_ip",\n'    '      "node_type": "ApiConnection",\n'    '      "code_content": "type: ApiConnection\\ninputs:\\n  body: SigninLogs | where IPAddress == _ip\\n",\n'    '      "statuses": ["Succeeded"]\n'    '    }\n'    '  ],\n'    '  "changelog": [\n'    '    {\n'    '      "change_type": "new_feature",\n'    '      "summary": "Spliced enrich_ip action between trigger and downstream steps."\n'    '    }\n'    '  ]\n'    '}\n'    + T_FENCE + '\n\n'    '### SPLICE_NODE — Topological Step Insertion\n\n'    '- filename (required): Path to the workflow directory containing the numbered steps\n'    '- revision_type (required): SPLICE_NODE\n'    '- after_id (optional): Action ID or folder slug to insert after (omit or null for 01_)\n'    '- new_id (required): Identifier for the new node (e.g. enrich_ip)\n'    '- node_type (optional): Action type (e.g. ApiConnection, Http, Compose, default step)\n'    '- code_content (required): Raw YAML definition of the step\n'    '- statuses (optional): Array of runAfter triggers (default ["Succeeded"])\n\n'    'Automated Behaviors:\n'    '- Shifts all subsequent file prefixes automatically (02_ -> 03_, etc.).\n'    '- Updates runAfter dependencies in downstream sibling steps to point to new_id.\n'    '- Validates DAG acyclicity using Kahn algorithm before writing to disk.\n\n'    '### CELL_PATCH and REPLACE — Modifying Step Contents\n\n'    'To surgically modify keys within an existing YAML step file:\n'    + T_FENCE + 'json\n'    '{\n'    '  "filename": "workflows/02_enrich_ip.yaml",\n'    '  "revision_type": "CELL_PATCH",\n'    '  "cell_id": "top:inputs:start",\n'    '  "code_content": "# %% [top:inputs:start]\\ninputs:\\n  body: SigninLogs | take 100\\n# %% [top:inputs:end]\\n"\n'    '}\n'    + T_FENCE + '\n\n'    'Use REPLACE with plain YAML to rewrite an entire step file.\n\n'    '### changelog[] — BLOCKING GATE\n\n'    'Every payload must include at least one valid changelog entry:\n'    '- change_type: new_feature | correcting_implementation | bug_fix | refactor | schema_migration\n'    '- summary: Concise affirmative sentence describing the final state achieved.\n\n'    '## Step 3 — Hand off the JSON\n\n'    'Apply:\n'    + T_FENCE + 'bash\n'    'cellsmith patch patch.json .\n'    + T_FENCE + '\n\n'    'Rollback:\n'    + T_FENCE + 'bash\n'    'cellsmith rollback patch.json .\n'    + T_FENCE + '\n'
)

def write_node_skill_doc(project_root: Path) -> Path:
    from cellsmith.constants import SKILL_DOC_FILENAME
    project_root.mkdir(parents=True, exist_ok=True)
    path = project_root / SKILL_DOC_FILENAME
    path.write_text(NODE_SKILL_DOC_MARKDOWN, encoding='utf-8')
    return path
