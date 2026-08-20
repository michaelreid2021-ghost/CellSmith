# filepath: src/cellsmith/annotator.py
# %% [ai_schema:pointer]
# CellSmith-annotated file. Cells marked with `# %% [<cell_id>]`.
# To modify safely: load `CELLSMITH_PATCH_SCHEMA.md` at the project root
# for the full JSON patch schema (incl. required `changelog` block).
# Run `cellsmith status` first — if it errors, ignore these markers and
# edit the file directly per the user's request.
# %% [ai_schema:end]

# %% [module:init:start]
"""AST/YAML traversal: turn a source file into cell-delimited regions.

`CellAnnotator` walks a Python AST and records the `# %% [...]` markers a file
needs; `plan_insertions` wraps that (plus the YAML path and `knobs:`
protection) as a pure function; `annotate_file` applies the result to disk.

Annotation is always a full regeneration: the file is stripped of its existing
header and markers first, then re-marked from the AST. Markers are derived
data, so topping up an already-annotated file could emit a second marker with
an id that already exists — leaving two cells answering to one `cell_id`.
"""
# %% [module:init:end]

# %% [imports:start]
import ast
import logging
import re
from pathlib import Path
from typing import List, Tuple

from cellsmith.constants import FULL_SCHEMA_HEADER, POINTER_HEADER
from cellsmith.files import strip_lines
# %% [imports:end]


# %% [class:CellAnnotator:start]
class CellAnnotator(ast.NodeVisitor):
# %% [method:CellAnnotator.__init__:start]
    def __init__(self):
        self.insertions: List[Tuple[int, str]] = []
        self.current_class: str = ""
        self.function_depth: int = 0
# %% [method:CellAnnotator.__init__:end]

# %% [method:CellAnnotator.visit_ClassDef:start]
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        base_id = f"class:{node.name}"
        self.insertions.append((node.lineno, f"# %% [{base_id}:start]\n"))
        if hasattr(node, "end_lineno") and node.end_lineno:
            self.insertions.append((node.end_lineno + 1, f"# %% [{base_id}:end]\n"))
        previous_class = self.current_class
        self.current_class = node.name
        self.generic_visit(node)
        self.current_class = previous_class
# %% [method:CellAnnotator.visit_ClassDef:end]

# %% [method:CellAnnotator.visit_FunctionDef:start]
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if self.function_depth == 0:
            if self.current_class:
                base_id = f"method:{self.current_class}.{node.name}"
            else:
                base_id = f"func:{node.name}"
            self.insertions.append((node.lineno, f"# %% [{base_id}:start]\n"))
            if hasattr(node, "end_lineno") and node.end_lineno:
                self.insertions.append((node.end_lineno + 1, f"# %% [{base_id}:end]\n"))
        self.function_depth += 1
        self.generic_visit(node)
        self.function_depth -= 1
# %% [method:CellAnnotator.visit_FunctionDef:end]

# %% [method:CellAnnotator.visit_AsyncFunctionDef:start]
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)
# %% [method:CellAnnotator.visit_AsyncFunctionDef:end]

# %% [method:CellAnnotator.visit_Module:start]
    def visit_Module(self, node: ast.Module) -> None:
        """Mark top-level statements into paired, contiguous cells.

        - Consecutive top-level imports form paired cells: `# %% [imports:start]` / `:end`
        - Top-level `if __name__ == '__main__':` forms paired `module:main_guard`
        - Other module-level clusters form paired `module:init` blocks
        """
        self.generic_visit(node)

        FUNC_CLASS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        IMPORT_NODES = (ast.Import, ast.ImportFrom)

        current_kind = None
        group_start_line = None
        group_end_line = None
        import_count = 0
        init_count = 0

        def _flush_group():
            nonlocal current_kind, group_start_line, group_end_line, import_count, init_count
            if current_kind is None or group_start_line is None:
                return
            if current_kind == "import":
                import_count += 1
                suffix = f":{import_count}" if import_count > 1 else ""
                cell_id = f"imports{suffix}"
            elif current_kind == "main_guard":
                cell_id = "module:main_guard"
            else:
                init_count += 1
                suffix = f":{init_count}" if init_count > 1 else ""
                cell_id = f"module:init{suffix}"

            self.insertions.append((group_start_line, f"# %% [{cell_id}:start]\n"))
            end_l = (group_end_line or group_start_line) + 1
            self.insertions.append((end_l, f"# %% [{cell_id}:end]\n"))
            current_kind = None
            group_start_line = None
            group_end_line = None

        for stmt in node.body:
            if isinstance(stmt, FUNC_CLASS):
                _flush_group()
                continue

            is_import = isinstance(stmt, IMPORT_NODES)
            is_main_guard = (
                isinstance(stmt, ast.If)
                and isinstance(stmt.test, ast.Compare)
                and isinstance(stmt.test.left, ast.Name)
                and stmt.test.left.id == "__name__"
                and any(isinstance(op, ast.Eq) for op in stmt.test.ops)
            )

            if is_main_guard:
                _flush_group()
                current_kind = "main_guard"
                group_start_line = stmt.lineno
                group_end_line = getattr(stmt, "end_lineno", stmt.lineno)
                _flush_group()
                continue

            if is_import:
                if current_kind != "import":
                    _flush_group()
                    current_kind = "import"
                    group_start_line = stmt.lineno
                group_end_line = getattr(stmt, "end_lineno", stmt.lineno)
            else:
                if current_kind != "init":
                    _flush_group()
                    current_kind = "init"
                    group_start_line = stmt.lineno
                group_end_line = getattr(stmt, "end_lineno", stmt.lineno)

        _flush_group()
# %% [method:CellAnnotator.visit_Module:end]
# %% [class:CellAnnotator:end]


# %% [func:_knob_ranges:start]
def _knob_ranges(lines: List[str]) -> List[Tuple[int, int]]:
    """1-based line ranges of user-authored `# %% [knobs:...]` blocks."""
    ranges = []
    in_knob = False
    start_line = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("# %% [knobs:") and stripped.endswith(":start]"):
            in_knob = True
            start_line = i + 1
        elif in_knob and stripped.startswith("# %% [knobs:") and stripped.endswith(":end]"):
            ranges.append((start_line, i + 1))
            in_knob = False
    return ranges
# %% [func:_knob_ranges:end]


# %% [func:plan_insertions:start]
def plan_insertions(lines: List[str], suffix: str) -> List[Tuple[int, str]]:
    """Return the `(lineno, marker)` pairs a clean annotation of `lines` needs.

    Pure and side-effect free. Markers that would land inside a protected
    `# %% [knobs:...]` block are filtered out. Shared by `annotate_file`,
    which applies them, and by the patch engine, which previews them when
    reporting an ambiguous marker.

    Raises SyntaxError when `suffix` is `.py` and the source doesn't parse.
    Returns [] for unsupported suffixes.
    """
    source = "".join(lines)
    knob_ranges = _knob_ranges(lines)

    if suffix == ".py":
        tree = ast.parse(source)
        annotator = CellAnnotator()
        annotator.visit(tree)
        raw_insertions = annotator.insertions
    elif suffix in (".yaml", ".yml"):
        raw_insertions = []
        current_key = None
        key_re = re.compile(r"^([a-zA-Z0-9_.-]+|'[^']+'|\"[^\"]+\")\s*:")
        for i, line in enumerate(lines):
            if line.startswith("#") or not line.strip():
                continue
            m = key_re.match(line)
            if m:
                new_key = m.group(1).strip("'\"")
                if current_key:
                    raw_insertions.append((i + 1, f"# %% [top:{current_key}:end]\n"))
                current_key = new_key
                raw_insertions.append((i + 1, f"# %% [top:{current_key}:start]\n"))
        if current_key:
            raw_insertions.append((len(lines) + 1, f"# %% [top:{current_key}:end]\n"))
    else:
        return []

    return [
        (lineno, marker)
        for lineno, marker in raw_insertions
        if not any(start <= lineno <= end for start, end in knob_ranges)
    ]
# %% [func:plan_insertions:end]


# %% [func:annotate_file:start]
def annotate_file(filepath: Path, header: str = FULL_SCHEMA_HEADER) -> None:
    """Regenerate `filepath`'s cell markers and schema header from its AST.

    Always strips first, so the result is a pure function of the file's code
    and the requested `header` — running this twice is a no-op, and it cannot
    produce a duplicate `cell_id`. The file is left untouched on disk when the
    regenerated content matches what's already there.
    """
    if not filepath.exists():
        logging.error(f"File not found: {filepath}")
        return

    if filepath.suffix not in (".py", ".yaml", ".yml"):
        return

    # Enforce pointer header for YAML files regardless of caller argument
    if filepath.suffix in (".yaml", ".yml"):
        header = POINTER_HEADER

    with open(filepath, "r", encoding="utf-8") as f:
        original = f.read()

    lines = strip_lines(
        original.splitlines(keepends=True), strip_prompt=True, strip_markers=True
    )

    try:
        insertions = plan_insertions(lines, filepath.suffix)
    except SyntaxError as e:
        logging.error(f"Syntax error in {filepath}: {e}")
        return

    # Process bottom-to-top to prevent line shifting issues.
    # Secondary key: at the same line number, `:end` markers must land ABOVE
    # `:start` markers in the final file (a block's end precedes the next
    # block's start). With reverse sort + insert-at-same-index semantics, the
    # item processed LAST ends up on top — so `:start` (key 1) must sort
    # before `:end` (key 0) in the reversed order.
    insertions.sort(key=lambda x: (x[0], 0 if ":end]" in x[1] else 1), reverse=True)

    for lineno, marker in insertions:
        lines.insert(lineno - 1, marker)

    lines.insert(0, f"# filepath: {filepath.as_posix()}\n" + header)
    new_content = "".join(lines)

    if new_content == original:
        logging.info(f"Already annotated, no changes: {filepath}")
        return

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(new_content)

    cells = sum(1 for _, m in insertions if ":start]" in m)
    logging.info(f"Annotated {filepath} with {cells} cell(s).")
# %% [func:annotate_file:end]
