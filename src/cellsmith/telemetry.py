# filepath: src/cellsmith/telemetry.py
# %% [ai_schema:pointer]
# CellSmith-annotated file. Cells marked with `# %% [<cell_id>]`.
# To modify safely: load `CELLSMITH_PATCH_SCHEMA.md` at the project root
# for the full JSON patch schema (incl. required `changelog` block).
# Run `cellsmith status` first — if it errors, ignore these markers and
# edit the file directly per the user's request.
# %% [ai_schema:end]

# %% [module:init:start]
"""Ephemeral focal telemetry: inject, read, and strip `@focal_trace`.

The decorator is written above the cell's `:start` marker, not inside it,
because that is where the annotator already places markers for a decorated
function. A later `CELL_PATCH` of the same cell therefore leaves the
instrumentation in place, and the agent keeps emitting pure logic.

Nothing here is meant to be committed. `finalize_file` removes every
decorator and the import preamble, returning the file to its pre-instrumented
state.
"""
# %% [module:init:end]

# %% [imports:start]
import ast
import logging
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple

from cellsmith.constants import load_template
from cellsmith.files import iter_target_files
# %% [imports:end]

# %% [module:init:2:start]
AGENTS_DIR = ".agents"
RUNTIME_FILENAME = "cellsmith_telemetry.py"
LOG_RELPATH = "logs/focal_session.jsonl"
DECORATOR = "focal_trace"

PREAMBLE_START = "# %% [knobs:cellsmith_telemetry:start]"
PREAMBLE_END = "# %% [knobs:cellsmith_telemetry:end]"

# Marked as a `knobs:` block so the annotator leaves it alone and `strip`
# preserves it; `finalize` removes it wholesale.
PREAMBLE = f"""{PREAMBLE_START}
import sys as _cs_sys
from pathlib import Path as _CsPath
for _cs_dir in _CsPath(__file__).resolve().parents:
    if (_cs_dir / "{AGENTS_DIR}").is_dir():
        _cs_sys.path.insert(0, str(_cs_dir / "{AGENTS_DIR}"))
        break
from {RUNTIME_FILENAME[:-3]} import {DECORATOR}
{PREAMBLE_END}
"""
# %% [module:init:2:end]


# %% [func:runtime_path:start]
def runtime_path(root: Path) -> Path:
    return root / AGENTS_DIR / RUNTIME_FILENAME
# %% [func:runtime_path:end]


# %% [func:log_path:start]
def log_path(root: Path) -> Path:
    return root / AGENTS_DIR / LOG_RELPATH
# %% [func:log_path:end]


# %% [func:ensure_runtime:start]
def ensure_runtime(root: Path) -> Path:
    """Write the telemetry runtime and make sure `.agents/` stays untracked."""
    target = runtime_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(load_template("telemetry_runtime.py.txt"), encoding="utf-8")

    gitignore = root / ".gitignore"
    entry = f"{AGENTS_DIR}/"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if entry not in existing.split():
        prefix = "" if existing.endswith("\n") or not existing else "\n"
        with open(gitignore, "a", encoding="utf-8") as handle:
            handle.write(f"{prefix}\n# CellSmith ephemeral agent telemetry\n{entry}\n")
        logging.info(f"Added {entry} to {gitignore}")
    return target
# %% [func:ensure_runtime:end]


# %% [func:_decorated_targets:start]
def _decorated_targets(source: str, cell_ids: Optional[Set[str]]) -> List[Tuple[int, str, str]]:
    """Find definitions to wrap: `(lineno, indent, cell_id)`, outermost only.

    Nested functions are part of their parent's cell and are never wrapped.
    """
    tree = ast.parse(source)
    found: List[Tuple[int, str, str]] = []

    def walk(node: ast.AST, class_name: str, depth: int) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, child.name, depth)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if depth == 0:
                    cell_id = (
                        f"method:{class_name}.{child.name}" if class_name
                        else f"func:{child.name}"
                    )
                    if cell_ids is None or cell_id in cell_ids:
                        found.append((child.lineno, child.col_offset, cell_id))
                walk(child, "", depth + 1)

    walk(tree, "", 0)
    return [(lineno, " " * col, cell_id) for lineno, col, cell_id in found]
# %% [func:_decorated_targets:end]


# %% [func:_already_traced:start]
def _already_traced(lines: List[str], index: int, indent: str) -> bool:
    """True when a `@focal_trace` decorator already sits above line `index`."""
    probe = index - 1
    while probe >= 0:
        stripped = lines[probe].strip()
        if not stripped or stripped.startswith("# %% ["):
            probe -= 1
            continue
        if stripped.startswith("@"):
            if stripped.startswith(f"@{DECORATOR}"):
                return True
            probe -= 1
            continue
        return False
    return False
# %% [func:_already_traced:end]


# %% [func:instrument_file:start]
def instrument_file(filepath: Path, cell_ids: Optional[Iterable[str]] = None) -> int:
    """Wrap the named cells in `@focal_trace`. Returns how many were wrapped.

    `cell_ids` of None instruments every top-level function and method.
    """
    if filepath.suffix != ".py" or not filepath.exists():
        return 0

    source = filepath.read_text(encoding="utf-8")
    wanted = set(cell_ids) if cell_ids is not None else None
    if wanted is not None:
        wanted = {c[: -len(":start")] if c.endswith(":start") else c for c in wanted}
        wanted = {c[: -len(":end")] if c.endswith(":end") else c for c in wanted}

    try:
        targets = _decorated_targets(source, wanted)
    except SyntaxError as e:
        logging.warning(f"telemetry: skipping unparseable {filepath}: {e}")
        return 0
    if not targets:
        return 0

    lines = source.splitlines(keepends=True)
    added = 0
    # Bottom-up so earlier line numbers stay valid.
    for lineno, indent, cell_id in sorted(targets, reverse=True):
        index = lineno - 1
        if _already_traced(lines, index, indent):
            continue
        # Decorators must sit above any that are already there.
        insert_at = index
        probe = index - 1
        while probe >= 0 and (
            lines[probe].strip().startswith("@") or not lines[probe].strip()
            or lines[probe].strip().startswith("# %% [")
        ):
            if lines[probe].strip().startswith("@"):
                insert_at = probe
            probe -= 1
        # Sit above the cell's own :start marker, so the decorator is outside
        # the cell. A later CELL_PATCH replaces :start through :end and would
        # otherwise strip the instrumentation the agent never emitted.
        start_marker = f"# %% [{cell_id}:start]"
        if insert_at > 0 and lines[insert_at - 1].strip() == start_marker:
            insert_at -= 1
        lines.insert(insert_at, f'{indent}@{DECORATOR}(cell_id="{cell_id}")\n')
        added += 1

    if not added:
        return 0

    if PREAMBLE_START not in source:
        lines.insert(_preamble_index(lines), PREAMBLE)

    filepath.write_text("".join(lines), encoding="utf-8")
    return added
# %% [func:instrument_file:end]


# %% [func:_preamble_index:start]
def _preamble_index(lines: List[str]) -> int:
    """Insert point for the import preamble: after the module docstring."""
    try:
        tree = ast.parse("".join(lines))
    except SyntaxError:
        return 0
    body = tree.body
    if not body:
        return 0
    first = body[0]
    if isinstance(first, ast.Expr) and isinstance(
        getattr(first, "value", None), ast.Constant
    ) and isinstance(first.value.value, str):
        return getattr(first, "end_lineno", first.lineno)
    # `lineno` on a decorated definition points at `def`, not at the first
    # decorator. Landing between the two would split the definition.
    lineno = first.lineno
    for decorator in getattr(first, "decorator_list", []):
        lineno = min(lineno, decorator.lineno)
    return lineno - 1
# %% [func:_preamble_index:end]


# %% [func:finalize_file:start]
def finalize_file(filepath: Path) -> int:
    """Remove every `@focal_trace` decorator and the import preamble.

    Returns the number of lines removed.
    """
    if filepath.suffix != ".py" or not filepath.exists():
        return 0

    lines = filepath.read_text(encoding="utf-8").splitlines(keepends=True)
    out: List[str] = []
    in_preamble = False
    for line in lines:
        stripped = line.strip()
        if stripped == PREAMBLE_START:
            in_preamble = True
            continue
        if in_preamble:
            if stripped == PREAMBLE_END:
                in_preamble = False
            continue
        if stripped.startswith(f"@{DECORATOR}"):
            continue
        out.append(line)

    removed = len(lines) - len(out)
    if removed:
        filepath.write_text("".join(out), encoding="utf-8")
    return removed
# %% [func:finalize_file:end]


# %% [func:finalize_tree:start]
def finalize_tree(
    target: Path,
    *,
    use_gitignore: bool = True,
    include_hidden: bool = False,
) -> Tuple[int, int]:
    """Strip instrumentation from every Python file under `target`.

    Returns `(files_changed, lines_removed)`.
    """
    files = 0
    lines = 0
    for filepath in iter_target_files(
        target, use_gitignore=use_gitignore, include_hidden=include_hidden
    ):
        removed = finalize_file(filepath)
        if removed:
            files += 1
            lines += removed
    return files, lines
# %% [func:finalize_tree:end]
