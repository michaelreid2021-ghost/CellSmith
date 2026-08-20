# filepath: src/cellsmith/reader/graph.py
# %% [ai_schema:pointer]
# CellSmith-annotated file. Cells marked with `# %% [<cell_id>]`.
# To modify safely: load `CELLSMITH_PATCH_SCHEMA.md` at the project root
# for the full JSON patch schema (incl. required `changelog` block).
# Run `cellsmith status` first — if it errors, ignore these markers and
# edit the file directly per the user's request.
# %% [ai_schema:end]

# %% [module:init:start]
"""Static call and dependency graph over CellSmith cells.

Nodes are cells — the same units `cellsmith patch` addresses — so a graph
lookup and a `cell_id` in a patch payload always mean the same region of
code. Cell boundaries come from `plan_insertions`, the annotator's own
planner, rather than from markers in the file: the graph is therefore
identical whether or not the project has been annotated.

Edges are call edges, resolved statically and conservatively. A call that
cannot be resolved to a known cell is dropped rather than guessed at.
"""
# %% [module:init:end]

# %% [imports:start]
import ast
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from cellsmith.annotator import plan_insertions
from cellsmith.files import iter_target_files, strip_lines
# %% [imports:end]

# Cell kinds carrying executable definitions, i.e. possible call targets.
# %% [module:init:2:start]
CALLABLE_KINDS = ("func", "method", "class")

# How much of a function body a trace follows. A call site's level is the
# widest construct enclosing it, so a request only follows the call when its
# `trace_type` is at least that wide. Straight-line calls are always followed.
TRACE_LEVELS = {"linear": 0, "branching": 1, "loops": 2, "all": 2}
LINEAR, BRANCHING, LOOPS = 0, 1, 2
# %% [module:init:2:end]


@dataclass
# %% [class:CellNode:start]
class CellNode:
    """One addressable cell, plus whatever the AST could tell us about it."""

    cell_id: str
    filepath: Path
    kind: str
    start: int
    end: int
    source: str
    name: str = ""
    class_name: str = ""
    signature: str = ""
    docstring: Optional[str] = None
    # 1-based inclusive line span of the docstring within the stripped file
    doc_span: Optional[Tuple[int, int]] = None
    # name -> narrowest trace level at which the call is reachable
    calls: Dict[str, int] = field(default_factory=dict)
    self_calls: Dict[str, int] = field(default_factory=dict)

    @property
# %% [method:CellNode.key:start]
    def key(self) -> str:
        """Graph-wide identity: `path/to/file.py:cell_id`."""
        return f"{self.filepath.as_posix()}:{self.cell_id}"
# %% [method:CellNode.key:end]

    @property
# %% [method:CellNode.char_cost:start]
    def char_cost(self) -> int:
        """Character count ignoring whitespace, as a token proxy."""
        return len("".join(self.source.split()))
# %% [method:CellNode.char_cost:end]
# %% [class:CellNode:end]


@dataclass
# %% [class:FileIndex:start]
class FileIndex:
    """Per-file lookup tables used while resolving call names."""

    filepath: Path
    lines: List[str]
    imports_cell: Optional[str] = None
    # bound name -> dotted module path it was imported from
    import_sources: Dict[str, str] = field(default_factory=dict)
    # bare name -> cell_id, for definitions in this file
    local_defs: Dict[str, str] = field(default_factory=dict)
# %% [class:FileIndex:end]


# %% [func:_cell_spans:start]
def _cell_spans(insertions: List[Tuple[int, str]]) -> List[Tuple[str, int, int]]:
    """Pair `:start`/`:end` insertions into `(cell_id, start, end)` spans.

    Line numbers are 1-based into the stripped source; `end` is exclusive.
    """
    starts: Dict[str, int] = {}
    ends: Dict[str, int] = {}
    order: List[str] = []
    for lineno, marker in insertions:
        body = marker.strip()[len("# %% ["):-1]
        if body.endswith(":start"):
            cell_id = body[: -len(":start")]
            starts[cell_id] = lineno
            order.append(cell_id)
        elif body.endswith(":end"):
            ends[body[: -len(":end")]] = lineno
    return [(cid, starts[cid], ends.get(cid, starts[cid] + 1)) for cid in order]
# %% [func:_cell_spans:end]


# %% [func:_kind_of:start]
def _kind_of(cell_id: str) -> str:
    if cell_id.startswith("func:"):
        return "func"
    if cell_id.startswith("method:"):
        return "method"
    if cell_id.startswith("class:"):
        return "class"
    if cell_id.startswith("imports"):
        return "imports"
    return "module"
# %% [func:_kind_of:end]


# %% [func:_signature:start]
def _signature(node: ast.AST, lines: List[str]) -> str:
    """The definition header — everything up to the first body statement."""
    start = node.lineno
    body = getattr(node, "body", None)
    if not body:
        return lines[start - 1].rstrip("\n")
    stop = max(body[0].lineno - 1, start)
    return "".join(lines[start - 1: stop]).rstrip()
# %% [func:_signature:end]


# %% [func:_docstring_span:start]
def _docstring_span(node: ast.AST) -> Optional[Tuple[int, int]]:
    """1-based inclusive line span of `node`'s docstring, if it has one."""
    body = getattr(node, "body", None)
    if not body:
        return None
    first = body[0]
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        return (first.lineno, getattr(first, "end_lineno", first.lineno))
    return None
# %% [func:_docstring_span:end]


# %% [class:_CallCollector:start]
class _CallCollector(ast.NodeVisitor):
    """Collect called names within a definition.

    Nested functions are part of their parent's cell, so their calls belong to
    the parent and are collected. Methods are cells in their own right, so
    when collecting for a class the definitions in its body are skipped —
    otherwise a class would inherit every edge of every method it holds.
    """

# %% [method:_CallCollector.__init__:start]
    def __init__(self, skip_defs: bool = False):
        self.skip_defs = skip_defs
        self.calls: Dict[str, int] = {}
        self.self_calls: Dict[str, int] = {}
        self._level = LINEAR
# %% [method:_CallCollector.__init__:end]

# %% [method:_CallCollector._record:start]
    def _record(self, bucket: Dict[str, int], name: str) -> None:
        """Keep the narrowest level a name is reachable at."""
        current = bucket.get(name)
        if current is None or self._level < current:
            bucket[name] = self._level
# %% [method:_CallCollector._record:end]

# %% [method:_CallCollector._descend:start]
    def _descend(self, node: ast.AST, level: int) -> None:
        previous = self._level
        self._level = max(previous, level)
        self.generic_visit(node)
        self._level = previous
# %% [method:_CallCollector._descend:end]

# %% [method:_CallCollector.visit_If:start]
    def visit_If(self, node: ast.If) -> None:
        self._descend(node, BRANCHING)
# %% [method:_CallCollector.visit_If:end]

# %% [method:_CallCollector.visit_Try:start]
    def visit_Try(self, node: ast.Try) -> None:
        self._descend(node, BRANCHING)
# %% [method:_CallCollector.visit_Try:end]

# %% [method:_CallCollector.visit_For:start]
    def visit_For(self, node: ast.For) -> None:
        self._descend(node, LOOPS)
# %% [method:_CallCollector.visit_For:end]

# %% [method:_CallCollector.visit_AsyncFor:start]
    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._descend(node, LOOPS)
# %% [method:_CallCollector.visit_AsyncFor:end]

# %% [method:_CallCollector.visit_While:start]
    def visit_While(self, node: ast.While) -> None:
        self._descend(node, LOOPS)
# %% [method:_CallCollector.visit_While:end]

# %% [method:_CallCollector.visit_FunctionDef:start]
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if self.skip_defs:
            return
        self.generic_visit(node)
# %% [method:_CallCollector.visit_FunctionDef:end]

# %% [method:_CallCollector.visit_AsyncFunctionDef:start]
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)
# %% [method:_CallCollector.visit_AsyncFunctionDef:end]

# %% [method:_CallCollector.visit_ClassDef:start]
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if self.skip_defs:
            return
        self.generic_visit(node)
# %% [method:_CallCollector.visit_ClassDef:end]

# %% [method:_CallCollector.visit_Call:start]
    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name):
            self._record(self.calls, func.id)
        elif isinstance(func, ast.Attribute):
            value = func.value
            if isinstance(value, ast.Name) and value.id in ("self", "cls"):
                self._record(self.self_calls, func.attr)
            else:
                self._record(self.calls, func.attr)
        self.generic_visit(node)
# %% [method:_CallCollector.visit_Call:end]
# %% [class:_CallCollector:end]


# %% [class:_DefIndexer:start]
class _DefIndexer(ast.NodeVisitor):
    """Map definition start lines to their AST nodes and enclosing class."""

# %% [method:_DefIndexer.__init__:start]
    def __init__(self):
        self.defs: Dict[int, Tuple[ast.AST, str]] = {}
        self._class = ""
# %% [method:_DefIndexer.__init__:end]

# %% [method:_DefIndexer.visit_ClassDef:start]
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.defs[node.lineno] = (node, self._class)
        previous, self._class = self._class, node.name
        self.generic_visit(node)
        self._class = previous
# %% [method:_DefIndexer.visit_ClassDef:end]

# %% [method:_DefIndexer.visit_FunctionDef:start]
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.defs[node.lineno] = (node, self._class)
        previous, self._class = self._class, ""
        self.generic_visit(node)
        self._class = previous
# %% [method:_DefIndexer.visit_FunctionDef:end]

# %% [method:_DefIndexer.visit_AsyncFunctionDef:start]
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)
# %% [method:_DefIndexer.visit_AsyncFunctionDef:end]
# %% [class:_DefIndexer:end]


# %% [class:CellGraph:start]
class CellGraph:
    """Cells indexed by `file:cell_id`, with statically resolved call edges."""

# %% [method:CellGraph.__init__:start]
    def __init__(self, root: Path):
        self.root = root
        self.nodes: Dict[str, CellNode] = {}
        self.files: Dict[Path, FileIndex] = {}
        self.edges: Dict[str, Dict[str, int]] = {}
        self._module_index: Dict[str, Path] = {}
# %% [method:CellGraph.__init__:end]

    # ------------------------------------------------------------------ build
# %% [method:CellGraph.add_file:start]
    def add_file(self, filepath: Path) -> None:
        """Index every cell in `filepath`. Unparseable files are skipped."""
        rel = filepath.relative_to(self.root)
        try:
            raw = filepath.read_text(encoding="utf-8")
        except OSError as e:
            logging.warning(f"CellGraph: cannot read {rel}: {e}")
            return

        lines = strip_lines(raw.splitlines(keepends=True), strip_prompt=True, strip_markers=True)
        try:
            insertions = plan_insertions(lines, filepath.suffix)
            tree = ast.parse("".join(lines))
        except SyntaxError as e:
            logging.warning(f"CellGraph: skipping unparseable {rel}: {e}")
            return

        indexer = _DefIndexer()
        indexer.visit(tree)

        index = FileIndex(filepath=rel, lines=lines)
        self.files[rel] = index
        self._module_index[self._dotted(rel)] = rel

        for cell_id, start, end in _cell_spans(insertions):
            kind = _kind_of(cell_id)
            source = "".join(lines[start - 1: end - 1])
            node = CellNode(
                cell_id=cell_id, filepath=rel, kind=kind,
                start=start, end=end, source=source,
            )

            if kind == "imports" and index.imports_cell is None:
                index.imports_cell = cell_id

            definition = indexer.defs.get(start)
            if definition is not None and kind in CALLABLE_KINDS:
                ast_node, class_name = definition
                collector = _CallCollector(skip_defs=(kind == "class"))
                if kind == "class":
                    # Bases and decorators are the class's own dependencies;
                    # its methods are separate cells with their own edges.
                    for child in ast_node.bases + ast_node.decorator_list:
                        collector.visit(child)
                    for child in ast_node.body:
                        collector.visit(child)
                else:
                    collector.visit(ast_node)
                node.name = getattr(ast_node, "name", "")
                node.class_name = class_name
                node.signature = _signature(ast_node, lines)
                node.docstring = ast.get_docstring(ast_node)
                node.doc_span = _docstring_span(ast_node)
                node.calls = collector.calls
                node.self_calls = collector.self_calls
                # Methods are reachable by bare name too, but local_defs is a
                # flat namespace — only module-level names claim a slot.
                if not class_name:
                    index.local_defs.setdefault(node.name, cell_id)

            self.nodes[node.key] = node

        for stmt in tree.body:
            if isinstance(stmt, ast.ImportFrom) and stmt.module:
                for alias in stmt.names:
                    index.import_sources[alias.asname or alias.name] = stmt.module
            elif isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    index.import_sources[alias.asname or alias.name.split(".")[0]] = alias.name
# %% [method:CellGraph.add_file:end]

# %% [method:CellGraph._dotted:start]
    def _dotted(self, rel: Path) -> str:
        return ".".join(rel.with_suffix("").parts)
# %% [method:CellGraph._dotted:end]

    # --------------------------------------------------------------- resolve
# %% [method:CellGraph._lookup_module:start]
    def _lookup_module(self, dotted: str) -> Optional[Path]:
        """Find the indexed file for a dotted module path, by suffix match."""
        if dotted in self._module_index:
            return self._module_index[dotted]
        for known, rel in self._module_index.items():
            if known == dotted or known.endswith("." + dotted):
                return rel
        return None
# %% [method:CellGraph._lookup_module:end]

# %% [method:CellGraph._resolve_name:start]
    def _resolve_name(self, name: str, origin: CellNode) -> Optional[str]:
        """Resolve a called `name` seen inside `origin` to a node key."""
        index = self.files.get(origin.filepath)
        if index is None:
            return None

        # 1. A definition in the same file.
        local = index.local_defs.get(name)
        if local:
            key = f"{origin.filepath.as_posix()}:{local}"
            if key in self.nodes:
                return key

        # 2. A name imported into this file from another indexed module.
        dotted = index.import_sources.get(name)
        if dotted:
            target_file = self._lookup_module(dotted)
            if target_file is not None:
                target_index = self.files[target_file]
                cell_id = target_index.local_defs.get(name)
                if cell_id:
                    return f"{target_file.as_posix()}:{cell_id}"

        # 3. A module-level definition that is unique across the project.
        matches = [
            key for key, node in self.nodes.items()
            if node.name == name and not node.class_name and node.kind in CALLABLE_KINDS
        ]
        if len(matches) == 1:
            return matches[0]

        # 4. `obj.method()` where the receiver's type is not statically known.
        # Only linked when exactly one method project-wide carries the name —
        # anything more and the edge would be a guess.
        methods = [
            key for key, node in self.nodes.items()
            if node.name == name and node.class_name and node.kind == "method"
        ]
        if len(methods) == 1:
            return methods[0]
        return None
# %% [method:CellGraph._resolve_name:end]

# %% [method:CellGraph._resolve_self_call:start]
    def _resolve_self_call(self, attr: str, origin: CellNode) -> Optional[str]:
        """Resolve `self.attr()` to a sibling method of the same class."""
        if not origin.class_name:
            return None
        key = f"{origin.filepath.as_posix()}:method:{origin.class_name}.{attr}"
        return key if key in self.nodes else None
# %% [method:CellGraph._resolve_self_call:end]

# %% [method:CellGraph.resolve_edges:start]
    def resolve_edges(self) -> None:
        """Populate `edges` once every file has been added.

        Each edge carries the narrowest trace level that reaches it, so a
        `linear` request can follow straight-line calls while ignoring those
        that only occur inside a branch or a loop.
        """
        self.edges = {}
        for key, node in self.nodes.items():
            targets: Dict[str, int] = {}

            def _add(found: Optional[str], level: int) -> None:
                if not found or found == key:
                    return
                if found not in targets or level < targets[found]:
                    targets[found] = level

            for attr, level in node.self_calls.items():
                _add(self._resolve_self_call(attr, node), level)
            for name, level in node.calls.items():
                _add(self._resolve_name(name, node), level)
            self.edges[key] = targets
# %% [method:CellGraph.resolve_edges:end]

    # ----------------------------------------------------------------- query
# %% [method:CellGraph.resolve_entry:start]
    def resolve_entry(self, entry: str) -> str:
        """Normalize an entry string to a node key.

        Accepts `file.py:func:name`, the `:start`/`:end` suffixed form, or a
        bare `func:name` when it is unambiguous project-wide.
        """
        candidate = entry.strip()
        for suffix in (":start", ":end"):
            if candidate.endswith(suffix):
                candidate = candidate[: -len(suffix)]

        if candidate in self.nodes:
            return candidate

        matches = [key for key in self.nodes if key.endswith(":" + candidate)]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise KeyError(f"entry {entry!r} does not match any cell")
        raise KeyError(
            f"entry {entry!r} is ambiguous across {len(matches)} cells: "
            f"{', '.join(sorted(matches)[:5])}"
        )
# %% [method:CellGraph.resolve_entry:end]

# %% [method:CellGraph.layers:start]
    def layers(
        self,
        entry_key: str,
        depth: int,
        *,
        trace_type: str = "linear",
        exclude: Optional[Set[str]] = None,
        seen: Optional[Set[str]] = None,
    ) -> List[List[str]]:
        """Breadth-first call layers from `entry_key`, nearest first.

        `layers[0]` is the entry itself. Each later layer is one more hop down
        the call graph; a node appears only in the layer that first reaches
        it. `trace_type` caps how wide a call site may be to be followed, and
        `exclude` prunes branches entirely. Pass `seen` to continue an
        existing traversal — the set is updated in place.
        """
        visited = seen if seen is not None else set()
        visited.add(entry_key)
        return [[entry_key]] + self.expand(
            [entry_key], depth, trace_type=trace_type, exclude=exclude, seen=visited
        )
# %% [method:CellGraph.layers:end]

# %% [method:CellGraph.expand:start]
    def expand(
        self,
        frontier: List[str],
        depth: int,
        *,
        trace_type: str = "linear",
        exclude: Optional[Set[str]] = None,
        seen: Optional[Set[str]] = None,
    ) -> List[List[str]]:
        """Walk `depth` further hops out from `frontier`.

        Returns one list per hop, excluding the frontier itself. `seen` is
        updated in place so successive calls keep widening the same traversal
        rather than revisiting nodes already rendered at a higher fidelity.
        """
        max_level = TRACE_LEVELS.get(trace_type, LINEAR)
        excluded = exclude or set()
        visited = seen if seen is not None else set(frontier)
        visited.update(frontier)

        result: List[List[str]] = []
        current = list(frontier)
        for _ in range(max(depth, 0)):
            nxt: List[str] = []
            for key in current:
                for target, level in sorted(self.edges.get(key, {}).items()):
                    if level > max_level or target in visited:
                        continue
                    if self.is_excluded(target, excluded):
                        continue
                    visited.add(target)
                    nxt.append(target)
            if not nxt:
                break
            result.append(nxt)
            current = nxt
        return result
# %% [method:CellGraph.expand:end]

# %% [method:CellGraph.is_excluded:start]
    def is_excluded(self, key: str, excluded: Set[str]) -> bool:
        """True when `key` matches any exclusion, by full key or bare cell_id."""
        if not excluded:
            return False
        node = self.nodes.get(key)
        cell_id = node.cell_id if node else key.split(":", 1)[-1]
        for raw in excluded:
            candidate = raw
            for suffix in (":start", ":end"):
                if candidate.endswith(suffix):
                    candidate = candidate[: -len(suffix)]
            if candidate == key or candidate == cell_id:
                return True
        return False
# %% [method:CellGraph.is_excluded:end]

# %% [method:CellGraph.imports_for:start]
    def imports_for(self, filepath: Path) -> Optional[CellNode]:
        """The file's top-level imports cell, so rendered slices stay valid."""
        index = self.files.get(filepath)
        if index is None or index.imports_cell is None:
            return None
        return self.nodes.get(f"{filepath.as_posix()}:{index.imports_cell}")
# %% [method:CellGraph.imports_for:end]
# %% [class:CellGraph:end]


# %% [func:build_graph:start]
def build_graph(
    target: Path,
    *,
    use_gitignore: bool = True,
    include_hidden: bool = False,
) -> CellGraph:
    """Index every Python file under `target` and resolve call edges."""
    root = target if target.is_dir() else target.parent
    graph = CellGraph(root)
    for filepath in iter_target_files(
        target, use_gitignore=use_gitignore, include_hidden=include_hidden
    ):
        if filepath.suffix == ".py":
            graph.add_file(filepath)
    graph.resolve_edges()
    return graph
# %% [func:build_graph:end]
