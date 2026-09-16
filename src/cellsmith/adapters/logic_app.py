# filepath: src/cellsmith/adapters/logic_app.py
# %% [ai_schema:pointer]
# CellSmith workflow DAG node. Cells marked with `# %% [<cell_id>]`.
# To modify or splice: load `CELLSMITH_PATCH_SCHEMA.md` at the project root
# for the workflow DAG patch schema (incl. SPLICE_NODE and changelog rules).
# Run `cellsmith status` first — if it errors, edit files directly.
# %% [ai_schema:end]
# %% [imports:start]
import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
import yaml

from cellsmith.adapters.lexicon import (
    apply_lexicon_to_actions,
    generate_lexicon,
)
# %% [imports:end]

# %% [module:init:start]
COMPOUND_TYPES = {"scope", "foreach", "until"}
# %% [module:init:end]


# %% [func:topological_sort_actions:start]
def topological_sort_actions(actions: Dict[str, dict]) -> List[str]:
    graph: Dict[str, Set[str]] = {name: set() for name in actions}
    in_degree: Dict[str, int] = {name: 0 for name in actions}

    for name, body in actions.items():
        if not isinstance(body, dict):
            continue
        run_after = body.get("runAfter", {})
        if isinstance(run_after, dict):
            for dep in run_after.keys():
                if dep in graph:
                    graph[dep].add(name)
                    in_degree[name] += 1

    queue = sorted([name for name, deg in in_degree.items() if deg == 0])
    ordered: List[str] = []

    while queue:
        curr = queue.pop(0)
        ordered.append(curr)
        for neighbor in sorted(graph[curr]):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)
        queue.sort()

    for name in sorted(actions.keys()):
        if name not in ordered:
            ordered.append(name)

    return ordered
# %% [func:topological_sort_actions:end]


# %% [func:unpack_actions_block:start]
def unpack_actions_block(actions: Dict[str, dict], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    sorted_names = topological_sort_actions(actions)

    for idx, name in enumerate(sorted_names, 1):
        node_data = actions[name]
        if not isinstance(node_data, dict):
            continue

        action_type = str(node_data.get("type", "")).lower()
        prefix = f"{idx:02d}_{name}"

        if action_type in COMPOUND_TYPES and "actions" in node_data:
            container_dir = output_dir / prefix
            container_dir.mkdir(parents=True, exist_ok=True)
            
            config = {k: v for k, v in node_data.items() if k != "actions"}
            config["id"] = name
            with open(container_dir / "config.yaml", "w", encoding="utf-8") as f:
                yaml.safe_dump(config, f, sort_keys=False)
                
            unpack_actions_block(node_data["actions"], container_dir)

        elif action_type == "if":
            container_dir = output_dir / prefix
            container_dir.mkdir(parents=True, exist_ok=True)
            
            config = {k: v for k, v in node_data.items() if k not in ("actions", "else")}
            config["id"] = name
            with open(container_dir / "config.yaml", "w", encoding="utf-8") as f:
                yaml.safe_dump(config, f, sort_keys=False)
                
            if "actions" in node_data and isinstance(node_data["actions"], dict):
                unpack_actions_block(node_data["actions"], container_dir / "true")
                
            if "else" in node_data and isinstance(node_data["else"], dict):
                false_dir = container_dir / "false"
                false_dir.mkdir(parents=True, exist_ok=True)
                else_actions = node_data["else"].get("actions", {})
                if isinstance(else_actions, dict):
                    unpack_actions_block(else_actions, false_dir)

        elif action_type == "switch":
            container_dir = output_dir / prefix
            container_dir.mkdir(parents=True, exist_ok=True)
            
            config = {k: v for k, v in node_data.items() if k not in ("cases", "default")}
            config["id"] = name
            with open(container_dir / "config.yaml", "w", encoding="utf-8") as f:
                yaml.safe_dump(config, f, sort_keys=False)
                
            cases = node_data.get("cases", {})
            if isinstance(cases, dict):
                for case_key, case_body in cases.items():
                    if isinstance(case_body, dict):
                        case_dir = container_dir / "cases" / case_key
                        case_dir.mkdir(parents=True, exist_ok=True)
                        case_config = {k: v for k, v in case_body.items() if k != "actions"}
                        if case_config:
                            with open(case_dir / "config.yaml", "w", encoding="utf-8") as f:
                                yaml.safe_dump(case_config, f, sort_keys=False)
                        if "actions" in case_body and isinstance(case_body["actions"], dict):
                            unpack_actions_block(case_body["actions"], case_dir)
                            
            default_block = node_data.get("default", {})
            if isinstance(default_block, dict):
                default_dir = container_dir / "default"
                default_dir.mkdir(parents=True, exist_ok=True)
                default_config = {k: v for k, v in default_block.items() if k != "actions"}
                if default_config:
                    with open(default_dir / "config.yaml", "w", encoding="utf-8") as f:
                        yaml.safe_dump(default_config, f, sort_keys=False)
                if "actions" in default_block and isinstance(default_block["actions"], dict):
                    unpack_actions_block(default_block["actions"], default_dir)

        else:
            node_file = output_dir / f"{prefix}.yaml"
            payload = dict(node_data)
            payload["id"] = name
            with open(node_file, "w", encoding="utf-8") as f:
                yaml.safe_dump(payload, f, sort_keys=False)
# %% [func:unpack_actions_block:end]


# %% [func:pack_actions_block:start]
def pack_actions_block(input_dir: Path) -> Dict[str, dict]:
    if not input_dir.exists() or not input_dir.is_dir():
        return {}

    items = []
    for p in input_dir.iterdir():
        if p.name.startswith(".") or p.name.endswith(".bak"):
            continue
        parts = p.name.split("_", 1)
        if parts[0].isdigit():
            items.append((int(parts[0]), parts[1] if len(parts) > 1 else parts[0], p))

    items.sort(key=lambda x: x[0])
    actions_out: Dict[str, dict] = {}

    for _, slug, path in items:
        if path.is_file() and path.suffix in (".yaml", ".yml"):
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            action_name = data.get("id", slug)
            cleaned = {k: v for k, v in data.items() if k != "id"}
            actions_out[action_name] = cleaned

        elif path.is_dir():
            cfg_file = path / "config.yaml"
            if not cfg_file.exists():
                cfg_file = path / "config.yml"
            
            config = {}
            if cfg_file.exists():
                with open(cfg_file, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f) or {}
            
            action_name = config.get("id", slug)
            action_type = str(config.get("type", "")).lower()
            cleaned = {k: v for k, v in config.items() if k != "id"}

            if action_type in COMPOUND_TYPES:
                cleaned["actions"] = pack_actions_block(path)
            elif action_type == "if":
                cleaned["actions"] = pack_actions_block(path / "true")
                false_dir = path / "false"
                if false_dir.exists():
                    cleaned["else"] = {"actions": pack_actions_block(false_dir)}
            elif action_type == "switch":
                cases_dir = path / "cases"
                if cases_dir.exists():
                    cleaned["cases"] = {}
                    for case_path in sorted(cases_dir.iterdir(), key=lambda p: p.name):
                        if case_path.is_dir() and not case_path.name.startswith("."):
                            case_cfg_file = case_path / "config.yaml"
                            case_obj = {}
                            if case_cfg_file.exists():
                                with open(case_cfg_file, "r", encoding="utf-8") as f:
                                    case_obj = yaml.safe_load(f) or {}
                            case_obj["actions"] = pack_actions_block(case_path)
                            cleaned["cases"][case_path.name] = case_obj
                default_dir = path / "default"
                if default_dir.exists():
                    default_cfg_file = default_dir / "config.yaml"
                    default_obj = {}
                    if default_cfg_file.exists():
                        with open(default_cfg_file, "r", encoding="utf-8") as f:
                            default_obj = yaml.safe_load(f) or {}
                    default_obj["actions"] = pack_actions_block(default_dir)
                    cleaned["default"] = default_obj

            actions_out[action_name] = cleaned

    return actions_out
# %% [func:pack_actions_block:end]


# %% [func:extract_descriptions:start]
def extract_descriptions(actions_dict: Dict[str, dict], name_to_id: Dict[str, str], lexicon_data: Dict[str, dict]) -> None:
    for action_name, body in actions_dict.items():
        if not isinstance(body, dict):
            continue
        aid = name_to_id.get(action_name)
        if aid:
            if "description" in body:
                lexicon_data[aid]["description"] = body.pop("description")
            else:
                lexicon_data[aid]["description"] = "<null>"
        
        for sub_key in ["actions", "else", "cases", "default"]:
            if sub_key in body:
                sub = body[sub_key]
                if sub_key == "else" and isinstance(sub, dict) and "actions" in sub:
                    extract_descriptions(sub["actions"], name_to_id, lexicon_data)
                elif sub_key == "cases" and isinstance(sub, dict):
                    for case_v in sub.values():
                        if isinstance(case_v, dict) and "actions" in case_v:
                            extract_descriptions(case_v["actions"], name_to_id, lexicon_data)
                elif sub_key == "default" and isinstance(sub, dict) and "actions" in sub:
                    extract_descriptions(sub["actions"], name_to_id, lexicon_data)
                elif sub_key == "actions" and isinstance(sub, dict):
                    extract_descriptions(sub, name_to_id, lexicon_data)
# %% [func:extract_descriptions:end]


# %% [func:inject_descriptions:start]
def inject_descriptions(actions_dict: Dict[str, dict], id_to_desc: Dict[str, str]) -> None:
    for action_id, body in actions_dict.items():
        if not isinstance(body, dict):
            continue
        if action_id in id_to_desc:
            body["description"] = id_to_desc[action_id]
        
        for sub_key in ["actions", "else", "cases", "default"]:
            if sub_key in body:
                sub = body[sub_key]
                if sub_key == "else" and isinstance(sub, dict) and "actions" in sub:
                    inject_descriptions(sub["actions"], id_to_desc)
                elif sub_key == "cases" and isinstance(sub, dict):
                    for case_v in sub.values():
                        if isinstance(case_v, dict) and "actions" in case_v:
                            inject_descriptions(case_v["actions"], id_to_desc)
                elif sub_key == "default" and isinstance(sub, dict) and "actions" in sub:
                    inject_descriptions(sub["actions"], id_to_desc)
                elif sub_key == "actions" and isinstance(sub, dict):
                    inject_descriptions(sub, id_to_desc)
# %% [func:inject_descriptions:end]


# %% [func:unpack_playbook:start]
def unpack_playbook(playbook_json_path: Path, output_dir: Path, intern: bool = False) -> dict:
    with open(playbook_json_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    is_wrapped = "definition" in raw_data
    definition = raw_data.get("definition", raw_data)
    actions = definition.get("actions", {})

    output_dir.mkdir(parents=True, exist_ok=True)
    stats = {}
    
    if intern:
        def get_dense_len(data):
            return len(re.sub(r'\s+', '', json.dumps(data, separators=(',', ':'))))
            
        orig_len = get_dense_len(actions)
        name_to_id = generate_lexicon(actions)
        
        lexicon_data = {}
        for name, aid in name_to_id.items():
            lexicon_data[aid] = {"name": name}
            
        extract_descriptions(actions, name_to_id, lexicon_data)
        
        with open(output_dir / "lexicon.yaml", "w", encoding="utf-8") as f:
            # Disable sort_keys to ensure 'name' naturally precedes 'description' in the YAML dump
            yaml.safe_dump(lexicon_data, f, sort_keys=False)
            
        actions = apply_lexicon_to_actions(actions, name_to_id, is_rehydrate=False)
        interned_len = get_dense_len(actions)
        saved = orig_len - interned_len
        
        stats = {
            "original_chars": orig_len,
            "interned_chars": interned_len,
            "saved_chars": saved,
            "reduction_pct": (saved / orig_len * 100) if orig_len > 0 else 0.0,
            "lexicon_entries": len(name_to_id)
        }

    meta = {
        "$schema": definition.get("$schema"),
        "contentVersion": definition.get("contentVersion"),
        "parameters": definition.get("parameters"),
        "triggers": definition.get("triggers"),
        "outputs": definition.get("outputs"),
        "_wrapper": {
            "is_wrapped": is_wrapped,
            "root_metadata": {k: v for k, v in raw_data.items() if k != "definition"} if is_wrapped else {},
        }
    }
    
    def_extra = {k: v for k, v in definition.items() if k not in ("actions", "$schema", "contentVersion", "parameters", "triggers", "outputs")}
    if def_extra:
        meta["_definition_extra"] = def_extra
        
    meta = {k: v for k, v in meta.items() if v is not None}

    with open(output_dir / "playbook_config.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(meta, f, sort_keys=False)

    unpack_actions_block(actions, output_dir)
    return stats
# %% [func:unpack_playbook:end]


# %% [func:pack_playbook:start]
def pack_playbook(workflow_dir: Path, output_json_path: Path) -> dict:
    config_file = workflow_dir / "playbook_config.yaml"
    meta = {}
    if config_file.exists():
        with open(config_file, "r", encoding="utf-8") as f:
            meta = yaml.safe_load(f) or {}

    wrapper_info = meta.get("_wrapper", {})
    is_wrapped = wrapper_info.get("is_wrapped", True)
    root_metadata = wrapper_info.get("root_metadata", {})

    packed_actions = pack_actions_block(workflow_dir)
    
    lexicon_file = workflow_dir / "lexicon.yaml"
    if lexicon_file.exists():
        with open(lexicon_file, "r", encoding="utf-8") as f:
            lexicon_data = yaml.safe_load(f) or {}
            
        id_to_name = {}
        id_to_desc = {}
        for k, v in lexicon_data.items():
            if isinstance(v, dict):
                id_to_name[k] = v.get("name", k)
                if "description" in v and v["description"] not in (None, "<null>"):
                    id_to_desc[k] = v["description"]
            else:
                id_to_name[k] = v
                
        if id_to_desc:
            inject_descriptions(packed_actions, id_to_desc)
            
        packed_actions = apply_lexicon_to_actions(packed_actions, id_to_name, is_rehydrate=True)
    
    definition = {}
    if "$schema" in meta:
        definition["$schema"] = meta["$schema"]
    if "contentVersion" in meta:
        definition["contentVersion"] = meta["contentVersion"]
    if "parameters" in meta:
        definition["parameters"] = meta["parameters"]
    if "triggers" in meta:
        definition["triggers"] = meta["triggers"]
    definition["actions"] = packed_actions
    if "outputs" in meta:
        definition["outputs"] = meta["outputs"]
    if "_definition_extra" in meta:
        definition.update(meta["_definition_extra"])

    if is_wrapped:
        assembled = dict(root_metadata)
        assembled["definition"] = definition
    else:
        assembled = definition

    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(assembled, f, indent=2, ensure_ascii=False)
        f.write("\n")

    return assembled
# %% [func:pack_playbook:end]


# %% [func:main:start]
def main():
    parser = argparse.ArgumentParser(description="Logic App JSON <-> Numbered YAML DAG Converter")
    subparsers = parser.add_subparsers(dest="command", required=True)

    unpack_parser = subparsers.add_parser("unpack", help="Deconstruct Logic App JSON actions into numbered YAML files")
    unpack_parser.add_argument("input_json", type=Path, help="Path to Logic App JSON or skeleton.json")
    unpack_parser.add_argument("output_dir", type=Path, help="Destination workflow directory")
    unpack_parser.add_argument("--intern", action="store_true", help="Compress action names to short tokens and build a lexicon map")

    pack_parser = subparsers.add_parser("pack", help="Assemble numbered YAML actions into Logic App JSON")
    pack_parser.add_argument("input_dir", type=Path, help="Source workflow directory")
    pack_parser.add_argument("output_json", type=Path, help="Destination JSON file")

    args = parser.parse_args()
    if args.command == "unpack":
        stats = unpack_playbook(args.input_json, args.output_dir, intern=args.intern)
        mode = " (with interning)" if args.intern else ""
        print(f"Successfully unpacked{mode} {args.input_json} to {args.output_dir}")
        if args.intern and stats:
            print("\nInterning Compression Report (non-whitespace characters):")
            print(f"  Original length:   {stats['original_chars']:,}")
            print(f"  Interned length:   {stats['interned_chars']:,}")
            print(f"  Characters saved:  {stats['saved_chars']:,} ({stats['reduction_pct']:.1f}%)")
            print(f"  Lexicon mapping:   {stats['lexicon_entries']:,} unique action IDs\n")
    elif args.command == "pack":
        pack_playbook(args.input_dir, args.output_json)
        print(f"Successfully assembled {args.input_dir} to {args.output_json}")
# %% [func:main:end]


# %% [module:main_guard:start]
if __name__ == "__main__":
    main()
# %% [module:main_guard:end]
