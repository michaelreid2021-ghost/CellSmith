# filepath: src/cellsmith/adapters/lexicon.py
# %% [ai_schema:pointer]
# CellSmith workflow DAG node. Cells marked with `# %% [<cell_id>]`.
# To modify or splice: load `CELLSMITH_PATCH_SCHEMA.md` at the project root
# for the workflow DAG patch schema (incl. SPLICE_NODE and changelog rules).
# Run `cellsmith status` first — if it errors, edit files directly.
# %% [ai_schema:end]
# %% [imports:start]
import re
from pathlib import Path
from typing import Any, Dict
import yaml
# %% [imports:end]

# %% [module:init:start]
EXPR_PATTERN = re.compile(
    r"(@(?:body|outputs|action|actions|item)\(\s*')([^']+?)('\s*\))"
)
# %% [module:init:end]


# %% [func:intern_expression_string:start]
def intern_expression_string(val: str, name_to_id: Dict[str, str]) -> str:
    def _sub(match: re.Match) -> str:
        prefix, name, suffix = match.groups()
        if name in name_to_id:
            return f"{prefix}{name_to_id[name]}{suffix}"
        return match.group(0)

    return EXPR_PATTERN.sub(_sub, val)
# %% [func:intern_expression_string:end]


# %% [func:rehydrate_expression_string:start]
def rehydrate_expression_string(val: str, id_to_name: Dict[str, str]) -> str:
    def _sub(match: re.Match) -> str:
        prefix, interned_id, suffix = match.groups()
        if interned_id in id_to_name:
            return f"{prefix}{id_to_name[interned_id]}{suffix}"
        return match.group(0)

    return EXPR_PATTERN.sub(_sub, val)
# %% [func:rehydrate_expression_string:end]


# %% [func:transform_values_only:start]
def transform_values_only(val: Any, lookup: Dict[str, str], is_rehydrate: bool) -> Any:
    if isinstance(val, dict):
        return {k: transform_values_only(v, lookup, is_rehydrate) for k, v in val.items()}
    elif isinstance(val, list):
        return [transform_values_only(v, lookup, is_rehydrate) for v in val]
    elif isinstance(val, str):
        fn = rehydrate_expression_string if is_rehydrate else intern_expression_string
        return fn(val, lookup)
    return val
# %% [func:transform_values_only:end]


# %% [func:apply_lexicon_to_actions:start]
def apply_lexicon_to_actions(actions: Dict[str, dict], lookup: Dict[str, str], is_rehydrate: bool) -> Dict[str, dict]:
    out = {}
    for action_key, action_body in actions.items():
        new_action_key = lookup.get(action_key, action_key)
        if isinstance(action_body, dict):
            out[new_action_key] = transform_node_body(action_body, lookup, is_rehydrate)
        else:
            out[new_action_key] = action_body
    return out
# %% [func:apply_lexicon_to_actions:end]


# %% [func:transform_node_body:start]
def transform_node_body(body: Dict[str, Any], lookup: Dict[str, str], is_rehydrate: bool) -> Dict[str, Any]:
    out = {}
    for k, v in body.items():
        if k == "runAfter" and isinstance(v, dict):
            out[k] = {lookup.get(rk, rk): rv for rk, rv in v.items()}
        elif k == "actions" and isinstance(v, dict):
            out[k] = apply_lexicon_to_actions(v, lookup, is_rehydrate)
        elif k == "else" and isinstance(v, dict) and "actions" in v:
            out[k] = {"actions": apply_lexicon_to_actions(v["actions"], lookup, is_rehydrate)}
            for ek, ev in v.items():
                if ek != "actions":
                    out[k][ek] = transform_values_only(ev, lookup, is_rehydrate)
        elif k == "cases" and isinstance(v, dict):
            out[k] = {}
            for ck, cv in v.items():
                out[k][ck] = transform_values_only(cv, lookup, is_rehydrate)
                if isinstance(cv, dict) and "actions" in cv:
                    out[k][ck]["actions"] = apply_lexicon_to_actions(cv["actions"], lookup, is_rehydrate)
        elif k == "default" and isinstance(v, dict) and "actions" in v:
            out[k] = transform_values_only(v, lookup, is_rehydrate)
            out[k]["actions"] = apply_lexicon_to_actions(v["actions"], lookup, is_rehydrate)
        else:
            out[k] = transform_values_only(v, lookup, is_rehydrate)
    return out
# %% [func:transform_node_body:end]


# %% [func:generate_lexicon:start]
def generate_lexicon(actions: Dict[str, dict], current_dict: Dict[str, str] = None, counter: list = None) -> Dict[str, str]:
    if current_dict is None:
        current_dict = {}
    if counter is None:
        counter = [1]
        
    for k, v in actions.items():
        if k not in current_dict:
            current_dict[k] = f"A{counter[0]:03d}"
            counter[0] += 1
        if isinstance(v, dict):
            for sub_key in ["actions", "else", "cases", "default"]:
                if sub_key in v:
                    sub = v[sub_key]
                    if sub_key == "else" and isinstance(sub, dict) and "actions" in sub:
                        generate_lexicon(sub["actions"], current_dict, counter)
                    elif sub_key == "cases" and isinstance(sub, dict):
                        for case_v in sub.values():
                            if isinstance(case_v, dict) and "actions" in case_v:
                                generate_lexicon(case_v["actions"], current_dict, counter)
                    elif sub_key == "default" and isinstance(sub, dict) and "actions" in sub:
                        generate_lexicon(sub["actions"], current_dict, counter)
                    elif sub_key == "actions" and isinstance(sub, dict):
                        generate_lexicon(sub, current_dict, counter)
    return current_dict
# %% [func:generate_lexicon:end]
