# filepath: src/cellsmith/survey.py
# %% [ai_schema:pointer]
# CellSmith-annotated file. Cells marked with `# %% [<cell_id>]`.
# To modify safely: load `CELLSMITH_PATCH_SCHEMA.md` at the project root
# for the full JSON patch schema (incl. required `changelog` block).
# Run `cellsmith status` first — if it errors, ignore these markers and
# edit the file directly per the user's request.
# %% [ai_schema:end]

# %% [module:init:start]
"""Project orientation tools for the `read` subcommand.

Four reports that answer "where do I start?" before a trace is compiled:

* `start_cell_report`      — probable entry-point cells, from manifest
  files (pyproject.toml, setup.cfg/setup.py, Dockerfiles) with a
  main.py/app.py fallback.
* `cell_list_report`       — the cells inside one supported file.
* `tree_report`            — the project's file tree, honoring
  `.gitignore` and `.ignore`, hidden files included.
* `file_contents_report`   — raw text of a file CellSmith does not parse.

They exist so an agent never reaches for `cat`, `head`, `ls` or `find`:
every orientation question has a CellSmith command, and every answer
points back at the cell-aware tools rather than at raw files.
"""
# %% [module:init:end]

# %% [imports:start]
import ast
import configparser
import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from cellsmith.annotator import plan_insertions
from cellsmith.constants import SUPPORTED_SUFFIXES
from cellsmith.files import build_ignore_spec, load_ignore_spec, strip_lines
from cellsmith.reader.graph import _cell_spans
from cellsmith.workspace import support_root
# %% [imports:end]

# Candidate ranks, lowest first: the closer a declaration is to "this is
# what runs", the more probable the entry point.
# %% [module:init:2:start]
RANK_PYPROJECT_SCRIPT = 0
RANK_DOCKERFILE = 1
RANK_SETUP_SCRIPT = 1
RANK_FALLBACK_SCRIPT = 2
RANK_DOCKERFILE_VARIANT = 3

# Safety caps so a pathological project cannot flood the agent's context.
TREE_ENTRY_CAP = 2000
FILE_CONTENT_CHAR_CAP = 100_000

# `name = module:func` — console script declarations, quoted or not.
_CONSOLE_TARGET_RE = re.compile(r"^\s*([\w.-]+)\s*=\s*[\"']?([\w.]+)(?::([\w]+))?")
_PYTHON_BIN_RE = re.compile(r"^python3?(\.\d+)?$")
_DOCKER_INSTR_RE = re.compile(r"^(ENTRYPOINT|CMD)\b\s*(.*)$", re.IGNORECASE)
# %% [module:init:2:end]


@dataclass(frozen=True)
# %% [class:_EntryCandidate:start]
class _EntryCandidate:
    """One probable entry point, with the manifest that declared it."""

    rank: int
    # `path/file.py:func:main`, or None when the declaration did not resolve
    cell: Optional[str]
    source: str
    note: str = ""
# %% [class:_EntryCandidate:end]


# %% [func:_toml_console_scripts:start]
def _toml_console_scripts(text: str) -> Dict[str, str]:
    """`name -> module:func` from [project.scripts] / [project.gui-scripts].

    Uses tomllib on 3.11+; on 3.10 (the project floor) it falls back to a
    section-scoped scan that only needs the two tables, which is all a
    console script declaration looks like.
    """
    data: Dict = {}
    try:
        import tomllib
    except ImportError:
        section: Optional[str] = None
        for line in text.splitlines():
            stripped = line.strip()
            heading = re.match(r"^\[(.+)\]\s*$", stripped)
            if heading:
                section = heading.group(1).strip()
                continue
            if section and section.startswith("project."):
                match = _CONSOLE_TARGET_RE.match(line)
                if match:
                    target = match.group(2) + (f":{match.group(3)}" if match.group(3) else "")
                    data.setdefault("project", {}).setdefault(section.split(".", 1)[1], {})[
                        match.group(1)
                    ] = target
    else:
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            return {}
    out: Dict[str, str] = {}
    project = data.get("project") if isinstance(data, dict) else None
    if isinstance(project, dict):
        for section in ("scripts", "gui-scripts"):
            table = project.get(section)
            if isinstance(table, dict):
                for name, target in table.items():
                    if isinstance(target, str):
                        out[name] = target
    return out
# %% [func:_toml_console_scripts:end]


# %% [func:_cfg_console_scripts:start]
def _cfg_console_scripts(text: str) -> Dict[str, str]:
    """`name -> module:func` from setup.cfg's [options.entry_points]."""
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(text)
    except configparser.Error:
        return {}
    out: Dict[str, str] = {}
    if not parser.has_section("options.entry_points"):
        return out
    for key, value in parser.items("options.entry_points"):
        if key == "console_scripts":
            for line in value.splitlines():
                match = _CONSOLE_TARGET_RE.match(line.strip())
                if match:
                    out[match.group(1)] = match.group(2) + (f":{match.group(3)}" if match.group(3) else "")
        elif re.match(r"^[\w.]+(?::[\w]+)?$", value.strip()):
            out[key] = value.strip()
    return out
# %% [func:_cfg_console_scripts:end]


# %% [func:_setup_py_console_scripts:start]
def _setup_py_console_scripts(text: str) -> Dict[str, str]:
    """Best-effort `name -> module:func` from a setup.py `entry_points` dict."""
    out: Dict[str, str] = {}
    marker = re.search(r"entry_points\s*=\s*\{", text)
    if not marker:
        return out
    depth, end = 1, len(text)
    for i in range(marker.end(), len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    block = text[marker.end():end]
    for match in re.finditer(r"['\"]?([\w.-]+)['\"]?\s*=\s*['\"]([\w.]+):([\w]+)['\"]", block):
        out[match.group(1)] = f"{match.group(2)}:{match.group(3)}"
    return out
# %% [func:_setup_py_console_scripts:end]


# %% [func:_is_dockerfile:start]
def _is_dockerfile(name: str) -> bool:
    lowered = name.lower()
    return (
        lowered == "dockerfile"
        or lowered.startswith("dockerfile.")
        or lowered.endswith(".dockerfile")
    )
# %% [func:_is_dockerfile:end]


# %% [func:_parse_dockerfile:start]
def _parse_dockerfile(text: str) -> Optional[List[str]]:
    """The effective runtime command: ENTRYPOINT tokens, extended by CMD.

    Later instructions override earlier ones, per Docker semantics. Both
    the JSON exec form and the shell form are handled.
    """
    logical: List[str] = []
    buffer = ""
    for line in text.splitlines():
        buffer = f"{buffer} {line.strip()}" if buffer else line
        if buffer.rstrip().endswith("\\"):
            buffer = buffer.rstrip()[:-1]
            continue
        logical.append(buffer)
        buffer = ""
    if buffer:
        logical.append(buffer)

    entrypoint: Optional[List[str]] = None
    cmd: Optional[List[str]] = None
    for line in logical:
        match = _DOCKER_INSTR_RE.match(line.strip())
        if not match:
            continue
        value = match.group(2).strip()
        if value.startswith("["):
            try:
                parsed = json.loads(value)
                tokens = [str(item) for item in parsed] if isinstance(parsed, list) else None
            except json.JSONDecodeError:
                tokens = None
            if tokens is None:
                continue
        else:
            try:
                tokens = shlex.split(value)
            except ValueError:
                tokens = value.split()
        if match.group(1).upper() == "ENTRYPOINT":
            entrypoint = tokens
        else:
            cmd = tokens
    if entrypoint is not None:
        return entrypoint + (cmd or [])
    return cmd
# %% [func:_parse_dockerfile:end]


# %% [func:_tokens_to_entry:start]
def _tokens_to_entry(tokens: List[str]) -> Optional[Tuple[str, str]]:
    """Reduce a runtime command to `(kind, value)`.

    `kind` is `module` (a `python -m` target), `file` (a `.py` path) or
    `command` (an installed executable name to match against declared
    console scripts). Wrapper tools (sh, env, uv run, python flags) are
    stepped over rather than guessed at; anything unrecognized yields
    None instead of a wrong answer.
    """
    i, n = 0, len(tokens)
    while i < n:
        base = tokens[i].rsplit("/", 1)[-1]
        if base in ("sh", "bash", "dash", "env"):
            i += 1
            if base == "env":
                while i < n and not tokens[i].startswith("-") and "=" in tokens[i]:
                    i += 1
            continue
        if base == "uv":
            j = i + 1
            while j < n and tokens[j].startswith("-"):
                j += 1
            if j < n and tokens[j] == "run":
                i = j + 1
                continue
            break
        if _PYTHON_BIN_RE.match(base):
            j = i + 1
            while j < n and tokens[j].startswith("-") and tokens[j] != "-m":
                j += 1
            if j < n and tokens[j] == "-m" and j + 1 < n:
                return ("module", tokens[j + 1])
            if j < n and tokens[j].endswith(".py"):
                # Keep the relative path whole — `python tools/main.py`
                # means the file in tools/, not the root.
                return ("file", tokens[j].removeprefix("./"))
            return None
        break
    if i < n:
        return ("command", tokens[i].rsplit("/", 1)[-1])
    return None
# %% [func:_tokens_to_entry:end]


# %% [func:_module_to_file:start]
def _module_to_file(root: Path, dotted: str) -> Optional[Path]:
    """Locate the file for a dotted module, trying common package layouts."""
    rel = Path(*dotted.split("."))
    for prefix in ("", "src", "python", "app", "lib"):
        base = root / prefix if prefix else root
        module_file = base / rel.with_suffix(".py")
        if module_file.is_file():
            return module_file
        package_init = base / rel / "__init__.py"
        if package_init.is_file():
            return package_init
    return None
# %% [func:_module_to_file:end]


# %% [func:_file_cells:start]
def _file_cells(filepath: Path) -> Optional[Tuple[List[str], List[Tuple[str, int, int]]]]:
    """`(stripped lines, cell spans)` for a supported file, or None.

    Spans are 1-based into the stripped source with `end` exclusive — the
    same coordinates the annotator's planner uses, so markers and code
    stay in agreement.
    """
    try:
        raw = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    lines = strip_lines(raw.splitlines(keepends=True))
    try:
        spans = _cell_spans(plan_insertions(lines, filepath.suffix))
    except SyntaxError:
        return None
    return lines, spans
# %% [func:_file_cells:end]


# %% [func:_entry_cell_for_file:start]
def _entry_cell_for_file(filepath: Path) -> Optional[str]:
    """The cell an agent should enter this file through.

    The `__main__` guard when present; otherwise the first module-level
    cluster that actually calls something; otherwise the first definition
    cell (a docstring-only init cluster has no edges to trace); otherwise
    the first init cell.
    """
    got = _file_cells(filepath)
    if got is None:
        return None
    lines, spans = got
    ids = [cid for cid, _, _ in spans]
    if "module:main_guard" in ids:
        return "module:main_guard"
    if filepath.suffix == ".py":
        try:
            tree = ast.parse("".join(lines))
        except SyntaxError:
            tree = None
        if tree is not None:
            for stmt in tree.body:
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                if not any(isinstance(node, ast.Call) for node in ast.walk(stmt)):
                    continue
                for cid, start, end in spans:
                    if start <= stmt.lineno < end and cid.startswith("module:"):
                        return cid
    init_cell = next((cid for cid in ids if cid.startswith("module:init")), None)
    if init_cell:
        def_cell = next(
            (cid for cid in ids if cid.startswith(("func:", "method:", "class:"))),
            None,
        )
        return def_cell or init_cell
    return ids[0] if ids else None
# %% [func:_entry_cell_for_file:end]


# %% [func:_cell_label:start]
def _cell_label(lines: List[str], start: int, end: int, limit: int = 72) -> str:
    """The first non-blank line of a cell span, for the cell list."""
    for line in lines[start - 1: end - 1]:
        if line.strip():
            label = line.strip()
            return label if len(label) <= limit else label[: limit - 1] + "…"
    return ""
# %% [func:_cell_label:end]


# %% [func:_candidate_from_target:start]
def _candidate_from_target(rank: int, source: str, target: str, root: Path) -> _EntryCandidate:
    """Resolve a `module` or `module:func` declaration to a cell, if possible."""
    module, _, func = target.partition(":")
    module, func = module.strip(), func.strip()
    if not module:
        return _EntryCandidate(rank, None, source, f"'{target}' is not a module declaration")
    filepath = _module_to_file(root, module)
    if filepath is None:
        return _EntryCandidate(
            rank, None, source,
            f"module '{module}' not found under the project (tried ., src/, python/, app/, lib/)",
        )
    try:
        rel = filepath.relative_to(root).as_posix()
    except ValueError:
        rel = filepath.name
    if not func and filepath.name == "__init__.py":
        # A bare module entry runs the package's __main__ when it has one.
        main_module = filepath.parent / "__main__.py"
        if main_module.is_file():
            filepath, rel = main_module, main_module.relative_to(root).as_posix()
    got = _file_cells(filepath)
    if got is None:
        return _EntryCandidate(rank, None, f"{source} -> {rel}", f"{rel} could not be parsed")
    ids = [cid for cid, _, _ in got[1]]
    if func:
        if f"func:{func}" in ids:
            return _EntryCandidate(rank, f"{rel}:func:{func}", source)
        return _EntryCandidate(
            rank, None, source,
            f"{rel} has no func:{func} cell (declared entry missing?); run --get-cell-list {rel}",
        )
    cell = _entry_cell_for_file(filepath)
    if cell:
        return _EntryCandidate(rank, f"{rel}:{cell}", source)
    return _EntryCandidate(rank, None, source, f"{rel} yielded no cells")
# %% [func:_candidate_from_target:end]


# %% [func:start_cell_report:start]
def start_cell_report(target: Path) -> str:
    """`--list-start-cell`: probable entry cells from manifests, then fallbacks."""
    root = (target if target.is_dir() else target.parent).resolve()
    if not root.is_dir():
        raise ValueError(f"target directory {target} does not exist")
    display = target.as_posix()
    console: Dict[str, str] = {}
    candidates: List[_EntryCandidate] = []

    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        scripts = _toml_console_scripts(pyproject.read_text(encoding="utf-8", errors="replace"))
        console.update(scripts)
        for name, value in sorted(scripts.items()):
            candidates.append(_candidate_from_target(
                RANK_PYPROJECT_SCRIPT,
                f"pyproject.toml [project.scripts] {name} = {value}", value, root,
            ))

    setup_cfg = root / "setup.cfg"
    if setup_cfg.is_file():
        for name, value in sorted(_cfg_console_scripts(setup_cfg.read_text(encoding="utf-8", errors="replace")).items()):
            candidates.append(_candidate_from_target(
                RANK_SETUP_SCRIPT, f"setup.cfg [options.entry_points] {name} = {value}", value, root,
            ))

    setup_py = root / "setup.py"
    if setup_py.is_file():
        for name, value in sorted(_setup_py_console_scripts(setup_py.read_text(encoding="utf-8", errors="replace")).items()):
            candidates.append(_candidate_from_target(
                RANK_SETUP_SCRIPT, f"setup.py entry_points {name} = {value}", value, root,
            ))

    for dockerfile in sorted(root.iterdir(), key=lambda p: (p.name != "Dockerfile", p.name)):
        if not (dockerfile.is_file() and _is_dockerfile(dockerfile.name)):
            continue
        try:
            text = dockerfile.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        tokens = _parse_dockerfile(text)
        if not tokens:
            continue
        command = " ".join(tokens)
        rank = RANK_DOCKERFILE if dockerfile.name == "Dockerfile" else RANK_DOCKERFILE_VARIANT
        entry = _tokens_to_entry(tokens)
        if entry is None:
            continue
        kind, value = entry
        if kind == "module":
            candidates.append(_candidate_from_target(rank, f"{dockerfile.name}: {command}", value, root))
        elif kind == "file":
            script = root / value
            if script.is_file():
                cell = _entry_cell_for_file(script)
                if cell:
                    candidates.append(_EntryCandidate(rank, f"{value}:{cell}", f"{dockerfile.name}: {command}"))
                else:
                    candidates.append(_EntryCandidate(rank, None, f"{dockerfile.name}: {command}", f"{value} yielded no cells"))
            else:
                candidates.append(_EntryCandidate(
                    rank, None, f"{dockerfile.name}: {command}", f"'{value}' not found at the project root",
                ))
        elif kind == "command":
            declared = console.get(value)
            if declared:
                candidates.append(_candidate_from_target(
                    rank, f"{dockerfile.name}: {command} (console script '{value}')", declared, root,
                ))
            else:
                candidates.append(_EntryCandidate(
                    rank, None, f"{dockerfile.name}: {command}",
                    f"'{value}' is not a console script declared in the manifests",
                ))

    # Fallback: conventional script names, when no manifest entry resolved.
    if not any(c.cell for c in candidates):
        hits: List[Path] = []
        for name in ("main.py", "app.py"):
            direct = root / name
            if direct.is_file():
                hits.append(direct)
        if not hits:
            for child in sorted(root.iterdir(), key=lambda p: p.name):
                if not child.is_dir() or child.name.startswith((".", "__")):
                    continue
                for name in ("main.py", "app.py"):
                    if (child / name).is_file():
                        hits.append(child / name)
        for filepath in hits:
            try:
                rel = filepath.relative_to(root).as_posix()
            except ValueError:
                rel = filepath.name
            cell = _entry_cell_for_file(filepath)
            if cell:
                candidates.append(_EntryCandidate(
                    RANK_FALLBACK_SCRIPT, f"{rel}:{cell}", f"fallback: {rel} (no manifest entry point)",
                ))

    # Deduplicate by cell, keeping the best rank and merging sources.
    merged: Dict[str, _EntryCandidate] = {}
    for candidate in sorted(candidates, key=lambda c: c.rank):
        key = candidate.cell or f"__unresolved__:{candidate.source}"
        held = merged.get(key)
        if held is None:
            merged[key] = candidate
        elif candidate.source not in held.source:
            merged[key] = _EntryCandidate(
                held.rank, held.cell, f"{held.source}; {candidate.source}", held.note,
            )

    resolved = sorted((c for c in merged.values() if c.cell), key=lambda c: (c.rank, c.cell))
    if resolved:
        out = [f"# START CELLS: {len(resolved)} candidate(s) for {display}, most probable first", "#"]
        for i, candidate in enumerate(resolved, 1):
            out.append(f"#   {i}. {candidate.cell}:start")
            out.append(f"#        source: {candidate.source}")
            if candidate.note:
                out.append(f"#        note:   {candidate.note}")
        top = resolved[0]
        out += ["#", f'# read with:  cellsmith read --entry "{top.cell}:start" --trace-depth 2 {display}']
        unresolved = [c for c in merged.values() if not c.cell]
        if unresolved:
            out.append(f"# ({len(unresolved)} declared but unresolved:")
            for candidate in unresolved[:3]:
                out.append(f"#     {candidate.source} — {candidate.note}")
        return "\n".join(out) + "\n"

    out = ["# NO SPECIFIC ENTRY POINT FOUND: no manifest entry point resolved, and no main.py/app.py."]
    for candidate in (c for c in merged.values() if not c.cell):
        out.append(f"#   declared but unresolved: {candidate.source} — {candidate.note}")
    py_files = sorted(p.name for p in root.glob("*.py") if p.is_file())
    spec = load_ignore_spec(root)
    support = support_root(target).resolve()
    folders: List[str] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or child.name == ".git" or child.resolve() == support:
            continue
        # Trailing slash: gitwildmatch patterns like `name/` match directories
        # only when the candidate path is marked as one.
        if spec is not None and spec.match_file(child.name + "/"):
            continue
        folders.append(f"{child.name}/")
    out += ["#"]
    if py_files:
        out.append("# Python files in the root directory:")
        out += [f"#   - {name}" for name in py_files]
    else:
        out.append("# No Python files in the root directory.")
    if folders:
        out += ["#", "# Folders (not git-ignored):"]
        out += [f"#   - {name}" for name in folders]
    out += [
        "#",
        f"# Next:  cellsmith read --get-cell-list <file> {display}    # list a file's cells",
        f'#        cellsmith read --entry "<file>:<cell>:start" --trace-depth 2 {display}',
    ]
    return "\n".join(out) + "\n"
# %% [func:start_cell_report:end]


# %% [func:cell_list_report:start]
def cell_list_report(target: Path, filename: str) -> str:
    """`--get-cell-list FILE`: the cells in one supported file, with a suggested entry."""
    root = (target if target.is_dir() else target.parent).resolve()
    if not root.is_dir():
        raise ValueError(f"target directory {target} does not exist")
    display_root = target.as_posix()
    path = Path(filename)
    full = path if path.is_absolute() else root / path
    if not full.is_file():
        raise ValueError(f"no such file: {full}")
    if full.suffix not in SUPPORTED_SUFFIXES:
        extension = full.suffix or "no extension"
        return (
            f"# UNSUPPORTED FILE TYPE: {full.name} ({extension}) is not a CellSmith-supported "
            f"type ({', '.join(SUPPORTED_SUFFIXES)}).\n"
            f"# Run:  cellsmith read --get-file-contents {full.name} {display_root}"
        )
    got = _file_cells(full)
    if got is None:
        raise ValueError(f"{full.name} could not be read or parsed (syntax error?)")
    lines, spans = got
    try:
        rel = full.relative_to(root).as_posix()
    except ValueError:
        rel = full.name
    out = [
        f"# CELLS: {rel} — {len(spans)} cell(s)",
        "# (line spans are into the source without CellSmith markers)",
        "#",
    ]
    if spans:
        width = max(len(cid) for cid, _, _ in spans)
        for cid, start, end in sorted(spans, key=lambda span: span[1]):
            out.append(f"#   {cid.ljust(width)}  L{start:>4}-{end - 1:<4}  {_cell_label(lines, start, end)}")
        if full.suffix == ".py":
            entry = _entry_cell_for_file(full)
            out += ["#"]
            if entry:
                out.append(f"# best entry:  {rel}:{entry}:start")
                out.append(f'# read with:   cellsmith read --entry "{rel}:{entry}:start" --trace-depth 2 {display_root}')
            else:
                out.append("# best entry:  none found (no module-level cells)")
    return "\n".join(out) + "\n"
# %% [func:cell_list_report:end]


# %% [func:tree_report:start]
def tree_report(target: Path) -> str:
    """`--tree`: the project's file tree.

    Honors `.gitignore` and `.ignore` at every level, accumulated from the
    root down git-style, includes intentionally hidden files, and always
    skips `.git` and CellSmith's own support tree.
    """
    root = (target if target.is_dir() else target.parent).resolve()
    if not root.is_dir():
        raise ValueError(f"target directory {target} does not exist")
    display = target.as_posix()
    support = support_root(target).resolve()
    accumulated: List[str] = []
    entries: List[Tuple[str, bool]] = []

    def read_ignores(directory: Path) -> None:
        for name in (".gitignore", ".ignore"):
            candidate = directory / name
            if candidate.is_file():
                try:
                    accumulated.extend(candidate.read_text(encoding="utf-8").splitlines(keepends=True))
                except (OSError, UnicodeDecodeError):
                    pass

    def walk(directory: Path, prefix: str) -> None:
        if len(entries) >= TREE_ENTRY_CAP:
            return
        spec = build_ignore_spec(accumulated)
        try:
            children = sorted(directory.iterdir(), key=lambda p: p.name)
        except (OSError, PermissionError):
            return
        for child in children:
            if len(entries) >= TREE_ENTRY_CAP:
                break
            if child.name == ".git":
                continue
            try:
                is_dir = child.is_dir()
            except OSError:
                is_dir = False
            if is_dir and child.resolve() == support:
                continue
            rel = prefix + child.name
            if is_dir:
                if child.name.startswith("__") and child.name.endswith("__"):
                    continue
                # Trailing slash: gitwildmatch patterns like `name/` match
                # directories only when the candidate path is marked as one.
                if spec is not None and spec.match_file(rel + "/"):
                    continue
                read_ignores(child)
                entries.append((rel + "/", True))
                walk(child, rel + "/")
            elif spec is None or not spec.match_file(rel):
                entries.append((rel, False))

    read_ignores(root)
    walk(root, "")
    out = [
        f"# TREE: {display} — {len(entries)} entries "
        f"(.gitignore + .ignore honored, hidden files included)",
    ]
    out += [("D " if is_dir else "F ") + rel for rel, is_dir in entries]
    if len(entries) >= TREE_ENTRY_CAP:
        out.append(f"# truncated at {TREE_ENTRY_CAP} entries")
    return "\n".join(out) + "\n"
# %% [func:tree_report:end]


# %% [func:file_contents_report:start]
def file_contents_report(target: Path, filename: str) -> str:
    """`--get-file-contents FILE`: raw text for types CellSmith does not parse.

    Supported types get an alert instead: their content is better served by
    the cell-aware tools, and this keeps agents off `cat`.
    """
    root = (target if target.is_dir() else target.parent).resolve()
    if not root.is_dir():
        raise ValueError(f"target directory {target} does not exist")
    display_root = target.as_posix()
    path = Path(filename)
    full = path if path.is_absolute() else root / path
    if not full.is_file():
        raise ValueError(f"no such file: {full}")
    try:
        rel = full.relative_to(root).as_posix()
    except ValueError:
        rel = full.name
    if full.suffix in SUPPORTED_SUFFIXES:
        entry = _entry_cell_for_file(full)
        out = [
            f"# ALERT: {full.name} is a CellSmith-supported type ({full.suffix}) — "
            f"the cell-aware tools are cheaper and patchable:",
            f"#   cellsmith read --get-cell-list {rel} {display_root}    # list its cells",
        ]
        if entry:
            out.append(
                f'#   cellsmith read --entry "{rel}:{entry}:start" --trace-depth 2 {display_root}    # read a slice'
            )
        out.append("# Raw contents are only served for unsupported file types.")
        return "\n".join(out) + "\n"
    try:
        text = full.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        try:
            size = full.stat().st_size
        except OSError:
            size = -1
        return f"# BINARY OR UNREADABLE FILE: {rel} ({size} bytes) is not valid UTF-8; contents not shown.\n"
    out = [f"# filepath: {rel} ({len(text)} chars)"]
    if len(text) > FILE_CONTENT_CHAR_CAP:
        out.append(f"# TRUNCATED to the first {FILE_CONTENT_CHAR_CAP} chars.")
        text = text[:FILE_CONTENT_CHAR_CAP]
    out.append(text.rstrip("\n"))
    return "\n".join(out) + "\n"
# %% [func:file_contents_report:end]


# %% [module:init:3:start]
__all__ = [
    "start_cell_report",
    "cell_list_report",
    "tree_report",
    "file_contents_report",
]
# %% [module:init:3:end]
