# filepath: src/cellsmith/reader/compiler.py
# %% [ai_schema:pointer]
# CellSmith-annotated file. Cells marked with `# %% [<cell_id>]`.
# To modify safely: load `CELLSMITH_PATCH_SCHEMA.md` at the project root
# for the full JSON patch schema (incl. required `changelog` block).
# Run `cellsmith status` first — if it errors, ignore these markers and
# edit the file directly per the user's request.
# %% [ai_schema:end]

# %% [module:init:start]
"""Render a slice of the CellGraph at mixed fidelity.

Three tiers, chosen by distance from the entry point:

* **Full** — the execution trace. Complete, marker-wrapped, patchable code,
  with the docstring removed: the agent is reading the implementation, and a
  summary of code it can already see is pure cost.
* **AST** — one or more layers beyond the trace. Signature and docstring
  only, body elided. The docstring is the whole point here, so it stays.
* **Laconic** — the far background. A single line naming the cell and what it
  depends on.

Cells are grouped under their file, each file preceded by its `# filepath:`
header and its imports cell, so that what the agent reads stays syntactically
coherent even though it is assembled from disjoint regions.
"""
# %% [module:init:end]

# %% [imports:start]
from pathlib import Path
from typing import Dict, List, Optional, Set

from cellsmith.constants import load_template
from cellsmith.reader.budget import Budget
from cellsmith.reader.graph import CellGraph, CellNode
from cellsmith.reader.schema import ReadRequest
# %% [imports:end]

# %% [module:init:2:start]
FULL, SKELETON, LACONIC = "full", "skeleton", "laconic"

TRUNCATION_NOTE = (
    "# [TRACE_TRUNCATED] - Exceeded read budget.\n"
    "# Execute CellRead with a new entry to expand if required.\n"
)
# %% [module:init:2:end]


# %% [func:_dedent_unit:start]
def _dedent_unit(source: str) -> str:
    """The indentation of a cell's first non-blank line."""
    for line in source.splitlines():
        if line.strip():
            return line[: len(line) - len(line.lstrip())]
    return ""
# %% [func:_dedent_unit:end]


# %% [func:_strip_docstring:start]
def _strip_docstring(node: CellNode) -> str:
    """The cell's source with its docstring lines removed."""
    if not node.doc_span:
        return node.source
    start, end = node.doc_span
    offset = node.start
    lines = node.source.splitlines(keepends=True)
    lo, hi = start - offset, end - offset + 1
    if lo < 0 or hi > len(lines):
        return node.source
    return "".join(lines[:lo] + lines[hi:])
# %% [func:_strip_docstring:end]


# %% [func:render_full:start]
def render_full(node: CellNode) -> str:
    """Complete code, wrapped in its cell markers so it can be patched."""
    body = _strip_docstring(node).rstrip("\n")
    return (
        f"# %% [{node.cell_id}:start]\n"
        f"{body}\n"
        f"# %% [{node.cell_id}:end]\n"
    )
# %% [func:render_full:end]


# %% [func:render_skeleton:start]
def render_skeleton(node: CellNode) -> str:
    """Signature and docstring, body elided."""
    if not node.signature:
        return render_laconic(node)

    indent = _dedent_unit(node.source)
    out = [f"# %% [{node.cell_id}:skeleton]\n", node.signature.rstrip() + "\n"]
    if node.docstring:
        summary = node.docstring.strip().splitlines()[0].strip()
        out.append(f'{indent}    """{summary}"""\n')
    out.append(f"{indent}    ...\n")
    return "".join(out)
# %% [func:render_skeleton:end]


# %% [func:render_laconic:start]
def render_laconic(node: CellNode, depends_on: Optional[List[str]] = None) -> str:
    """A single line: what the cell is, and what it leans on."""
    label = node.signature.strip() if node.signature else node.cell_id
    label = label.rstrip(":")
    line = f"# {node.cell_id} :: {label}"
    if depends_on:
        line += f" | Depends on: {', '.join(sorted(depends_on)[:6])}"
    return line + "\n"
# %% [func:render_laconic:end]


# %% [func:render_truncated:start]
def render_truncated(node: CellNode) -> str:
    """A breadcrumb standing in for a cell that did not fit the budget."""
    indent = _dedent_unit(node.source)
    header = node.signature.rstrip() if node.signature else f"# {node.cell_id}"
    out = [f"# %% [{node.cell_id}:truncated]\n", header + "\n"]
    for line in TRUNCATION_NOTE.splitlines():
        out.append(f"{indent}    {line}\n")
    if node.signature:
        out.append(f"{indent}    pass\n")
    return "".join(out)
# %% [func:render_truncated:end]


# %% [class:ReadCompiler:start]
class ReadCompiler:
    """Turns a `ReadRequest` into the text an agent should read."""

# %% [method:ReadCompiler.__init__:start]
    def __init__(self, graph: CellGraph, request: ReadRequest):
        self.graph = graph
        self.request = request
        self.budget = Budget(max_characters=request.max_characters)
        self.fidelity: Dict[str, str] = {}
        self.truncated: List[str] = []
# %% [method:ReadCompiler.__init__:end]

    # ------------------------------------------------------------- planning
# %% [method:ReadCompiler._pinned_keys:start]
    def _pinned_keys(self) -> Set[str]:
        pinned = set()
        for raw in self.request.trace_keep:
            try:
                pinned.add(self.graph.resolve_entry(raw))
            except KeyError:
                continue
        return pinned
# %% [method:ReadCompiler._pinned_keys:end]

# %% [method:ReadCompiler.plan:start]
    def plan(self) -> str:
        """Assign a fidelity to every reachable cell. Returns the entry key."""
        entry_key = self.graph.resolve_entry(self.request.entry)
        exclude = set(self.request.trace_exclude_paths)
        seen: Set[str] = set()

        trace = self.graph.layers(
            entry_key,
            self.request.trace_depth,
            trace_type=self.request.trace_type,
            exclude=exclude,
            seen=seen,
        )
        for layer in trace:
            for key in layer:
                self.fidelity[key] = FULL

        frontier = trace[-1] if trace else [entry_key]
        skeleton_layers = self.graph.expand(
            frontier,
            self.request.ast_layers,
            trace_type="all",
            exclude=exclude,
            seen=seen,
        )
        for layer in skeleton_layers:
            for key in layer:
                self.fidelity[key] = SKELETON

        frontier = skeleton_layers[-1] if skeleton_layers else frontier
        laconic_layers = self.graph.expand(
            frontier,
            self.request.laconic_background_layers,
            trace_type="all",
            exclude=exclude,
            seen=seen,
        )
        for layer in laconic_layers:
            for key in layer:
                self.fidelity[key] = LACONIC

        # Pins win over whatever tier distance assigned them.
        for key in self._pinned_keys():
            self.fidelity[key] = FULL

        return entry_key
# %% [method:ReadCompiler.plan:end]

    # ------------------------------------------------------------ rendering
# %% [method:ReadCompiler._class_shell:start]
    def _class_shell(self, node: CellNode) -> str:
        """A class cell without its methods, which are cells in their own right.

        Rendering the class whole would repeat every method that the trace
        already selected — the exact duplication this compiler exists to avoid.
        What remains is the header, the docstring-stripped class body, and a
        marker where the methods were.
        """
        nested = [
            other for other in self.graph.nodes.values()
            if other.filepath == node.filepath
            and other.key != node.key
            and other.start > node.start
            and other.end <= node.end
        ]
        if not nested:
            return _strip_docstring(node).rstrip("\n")

        # Work from the original source so the recorded line spans line up;
        # the docstring is dropped below, alongside the member cells.
        offset = node.start
        lines = node.source.splitlines(keepends=True)
        drop: Set[int] = set()
        for other in nested:
            for lineno in range(other.start, other.end):
                drop.add(lineno - offset)
        if node.doc_span:
            for lineno in range(node.doc_span[0], node.doc_span[1] + 1):
                drop.add(lineno - offset)

        kept = [line for i, line in enumerate(lines) if i not in drop]
        indent = _dedent_unit(node.source)
        body = "".join(kept).rstrip("\n")
        return f"{body}\n{indent}    # ... {len(nested)} member cell(s) rendered separately"
# %% [method:ReadCompiler._class_shell:end]

# %% [method:ReadCompiler._render_cell:start]
    def _render_cell(self, key: str, node: CellNode, pinned: bool) -> str:
        tier = self.fidelity[key]
        if tier == FULL and node.kind == "class":
            text = (
                f"# %% [{node.cell_id}:start]\n"
                f"{self._class_shell(node)}\n"
                f"# %% [{node.cell_id}:end]\n"
            )
            cost = len("".join(text.split()))
            if self.budget.charge(cost, key, pinned=pinned):
                return text
            self.truncated.append(key)
            return render_truncated(node)

        if tier == LACONIC:
            depends = [
                self.graph.nodes[t].name or self.graph.nodes[t].cell_id
                for t in self.graph.edges.get(key, {})
                if t in self.graph.nodes
            ]
            text = render_laconic(node, depends)
            self.budget.charge(len("".join(text.split())), key, pinned=True)
            return text

        renderer = render_full if tier == FULL else render_skeleton
        text = renderer(node)
        cost = len("".join(text.split()))
        if self.budget.charge(cost, key, pinned=pinned):
            return text

        self.truncated.append(key)
        return render_truncated(node)
# %% [method:ReadCompiler._render_cell:end]

# %% [method:ReadCompiler.compile:start]
    def compile(self) -> str:
        """Produce the full read payload."""
        entry_key = self.plan()
        pinned = self._pinned_keys() | {entry_key}

        # Group by file, preserving trace order across the whole payload.
        by_file: Dict[Path, List[str]] = {}
        for key in self.fidelity:
            node = self.graph.nodes.get(key)
            if node is None:
                continue
            by_file.setdefault(node.filepath, []).append(key)

        chunks: List[str] = [load_template("context_rules.txt"), "\n"]
        entry_node = self.graph.nodes[entry_key]
        chunks.append(
            f"# ENTRY: {entry_node.filepath.as_posix()}:{entry_node.cell_id}\n"
            f"# TRACE: depth={self.request.trace_depth} "
            f"type={self.request.trace_type} "
            f"ast_layers={self.request.ast_layers} "
            f"laconic_layers={self.request.laconic_background_layers}\n\n"
        )

        # The entry's file leads; the rest follow in path order.
        ordered = sorted(by_file, key=lambda p: (p != entry_node.filepath, p.as_posix()))

        for filepath in ordered:
            keys = sorted(
                by_file[filepath],
                key=lambda k: self.graph.nodes[k].start,
            )
            visible = [k for k in keys if self.fidelity[k] != LACONIC]

            chunks.append(f"# filepath: {filepath.as_posix()}\n")

            # Imports keep the rendered cells syntactically meaningful.
            if visible:
                imports = self.graph.imports_for(filepath)
                if imports is not None and imports.key not in self.fidelity:
                    self.budget.charge(imports.char_cost, imports.key, pinned=True)
                    chunks.append(render_full(imports))

            for key in keys:
                node = self.graph.nodes[key]
                chunks.append(self._render_cell(key, node, key in pinned))
            chunks.append("\n")

        for extra in self.request.include_files:
            path = self.graph.root / extra
            if not path.exists():
                chunks.append(f"# [MISSING INCLUDE] {extra}\n\n")
                continue
            text = path.read_text(encoding="utf-8")
            self.budget.charge(len("".join(text.split())), extra, pinned=True)
            chunks.append(f"# filepath: {extra}\n{text.rstrip()}\n\n")

        chunks.append(f"# BUDGET: {self.budget.summary()}\n")
        if self.truncated or self.budget.dropped:
            cut = sorted(set(self.truncated) | set(self.budget.dropped))
            chunks.append(f"# TRUNCATED CELLS: {len(cut)}\n")
            for key in cut:
                chunks.append(f"#   {key}\n")
        return "".join(chunks)
# %% [method:ReadCompiler.compile:end]
# %% [class:ReadCompiler:end]


# %% [func:compile_read:start]
def compile_read(graph: CellGraph, request: ReadRequest) -> str:
    """Compile `request` against `graph` and return the rendered context."""
    return ReadCompiler(graph, request).compile()
# %% [func:compile_read:end]
