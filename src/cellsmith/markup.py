# filepath: cellsmith.py
# %% [imports]
import argparse
import ast
import json
import logging
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple

# %% [module:init]
try:
    from cellsmith import __version__
except ImportError:
    __version__ = "unknown"  # script-mode fallback

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

CHANGELOG_FILE = "CHANGELOG.cellsmith.jsonl"
SKILL_DOC_FILENAME = "CELLSMITH_PATCH_SCHEMA.md"
VALID_CHANGE_TYPES = frozenset({
    "new_feature",
    "correcting_implementation",
    "bug_fix",
    "refactor",
    "schema_migration",
})

# Full in-file schema header — embedded by `cellsmith annotate` so the file
# self-documents the JSON patch contract. Used when the file may be pasted
# into a chat UI without any agent tooling around it.
FULL_SCHEMA_HEADER = (
    "# %% [ai_schema:instructions]\n"
    "# AI INSTRUCTIONS - PATCH SCHEMA:\n"
    "#\n"
    "# To modify this file, return a JSON response with the following structure.\n"
    "# When using CELL_PATCH, `cell_id` MUST be an exact marker that exists in the file.\n"
    "# Functions/classes/methods use paired markers: `func:name:start` / `func:name:end`\n"
    "# (likewise `class:X:start` and `method:Cls.x:start`). Patch the `:start` cell;\n"
    "# everything through the matching `:end` marker is replaced. Simple cells like\n"
    "# `imports` and `module:init` have a single marker with no suffix.\n"
    "#\n"
    "# {\n"
    "#   \"revisions\": [\n"
    "#     {\n"
    "#       \"filename\": \"path/to/this/file.py\",\n"
    "#       \"revision_type\": \"CELL_PATCH\",  # Or \"REPLACE\", \"CELL_CREATE\", \"FILE_CREATE\", \"FILE_MOVE\"\n"
    "#       \"cell_id\": \"func:my_function:start\",  # Match an exact marker (e.g. 'imports', 'func:x:start', 'method:Cls.x:start')\n"
    "#       \"code_content\": \"# %% [func:my_function:start]\\ndef my_function():\\n    pass\\n# %% [func:my_function:end]\\n\"\n"
    "#     }\n"
    "#   ],\n"
    "#   \"changelog\": [\n"
    "#     {\n"
    "#       \"change_type\": \"bug_fix\",   # Required. One of: new_feature, correcting_implementation, bug_fix, refactor, schema_migration\n"
    "#       \"summary\": \"Concise single-sentence description of the final state achieved.\",\n"
    "#       \"details\": [\"Optional bullet of granular technical change.\"]\n"
    "#     }\n"
    "#   ]\n"
    "# }\n"
    "#\n"
    "# BLOCKING GATE: every patch response MUST include at least one `changelog`\n"
    "# entry. Classify your work strictly using the `change_type` enum. Write the\n"
    "# `summary` as affirmative documentation of the final state — not a recount\n"
    "# of past conversational errors.\n"
    "#\n"
    "# Choose the most efficient tool for the job (the user pays per token):\n"
    "#   * REPLACE     : For total rewrites or files under 50 lines. Emit PLAIN\n"
    "#                   code only — no cell markers, no schema header. The tool\n"
    "#                   re-annotates the file automatically after a successful patch.\n"
    "#   * CELL_PATCH  : For surgical updates to a specific function/class/method.\n"
    "#                   `cell_id` MUST exist in the current SKELETON of the file.\n"
    "#                   `code_content` MUST be the COMPLETE cell — beginning with\n"
    "#                   its `:start` marker line and including the matching `:end`\n"
    "#                   marker — never a partial diff.\n"
    "#   * CELL_CREATE : To add new logic to an existing file. Plain code, no\n"
    "#                   markers. Optional `append_after`: an existing cell_id\n"
    "#                   (e.g. \"func:top:end\", \"method:Cls.run:end\") — the new\n"
    "#                   code is inserted right after that cell. Omitted: inserts\n"
    "#                   before `module:main_guard` (or EOF if none). To add a\n"
    "#                   METHOD, use append_after with a method of that class and\n"
    "#                   emit the code already indented for the class body.\n"
    "#   * FILE_CREATE : To create a brand new file. Plain code only — annotation\n"
    "#                   is handled by the tool, same as REPLACE.\n"
    "#   * FILE_MOVE   : To move/rename a file (requires 'new_filename' field).\n"
    "#\n"
    "# Nested (inner) functions have NO markers and are never patchable on their\n"
    "# own — they are part of their parent's cell. CELL_PATCH the parent.\n"
    "# Prefer nested functions only as a LAST RESORT (true closures). If you\n"
    "# encounter one that could be a module-level function or private method,\n"
    "# propose extracting it to the user ONCE — it makes the code directly\n"
    "# patchable. If the user declines, respect that and do not raise it again.\n"
    "#\n"
    "# `cellsmith patch` prints a numbered per-revision report (OK / FAILED).\n"
    "# If some revisions fail, re-emit ONLY the FAILED ones — the OK ones are\n"
    "# already applied and must not be re-sent.\n"
    "# %% [ai_schema:end]\n"
    "\n"
)

# Laconic pointer header — embedded by `cellsmith annotate-agent` instead of
# FULL_SCHEMA_HEADER, to avoid duplicating ~30 lines of schema instructions
# across every annotated file in an agentic workflow. The skill doc at the
# project root carries the full schema.
POINTER_HEADER = (
    "# %% [ai_schema:pointer]\n"
    "# CellSmith-annotated file. Cells marked with `# %% [<cell_id>]`.\n"
    "# To modify safely: load `" + SKILL_DOC_FILENAME + "` at the project root\n"
    "# for the full JSON patch schema (incl. required `changelog` block).\n"
    "# Run `cellsmith status` first — if it errors, ignore these markers and\n"
    "# edit the file directly per the user's request.\n"
    "# %% [ai_schema:end]\n"
    "\n"
)

# Full schema doc dropped at project root by `cellsmith annotate-agent`.
# Markdown so any agent (Claude Code, Cursor, Continue, chat-UI paste) can
# load it; idempotent — overwritten on every run from this single constant.
SKILL_DOC_MARKDOWN = r"""# CellSmith patch schema

This project's `.py` files have been annotated by **CellSmith** with cell
markers. Functions, classes, and methods use *paired* markers —
`# %% [func:name:start]` / `# %% [func:name:end]`, `# %% [class:Foo:start]`,
`# %% [method:Cls.x:start]`, etc. Simple cells (`# %% [imports]`,
`# %% [module:init]`, `# %% [module:main_guard]`) have a single marker.
Each annotated file opens with a short pointer to this document.

## Step 1 — probe before patching

Before generating any patch payload, run:

```bash
cellsmith status
```

- **If it prints `available`** (exit 0): generate a JSON patch per the schema
  below and save it as `patch.json`. The user (or your tooling) applies it
  with `cellsmith patch patch.json .`.
- **If it errors** (command not found, or exit non-zero): **ignore the cell
  markers entirely.** Edit the target file(s) directly using your normal
  tools per the user's original request. The markers are inert comments — no
  harm in leaving them in place.

## Step 2 — JSON patch shape

```json
{
  "revisions": [
    {
      "filename": "path/to/file.py",
      "revision_type": "CELL_PATCH",
      "cell_id": "method:UserService.validate:start",
      "code_content": "# %% [method:UserService.validate:start]\n    def validate(self, ...):\n        ...\n# %% [method:UserService.validate:end]\n"
    }
  ],
  "changelog": [
    {
      "change_type": "bug_fix",
      "summary": "validate() now rejects empty bearer tokens before hitting the DB.",
      "details": [
        "Replaced is None check with not token.strip()",
        "Added regression test for empty-string token"
      ]
    }
  ]
}
```

### `revisions[]` — surgical edits and file operations

| Field | Required | Notes |
|---|---|---|
| `filename` | yes | Path relative to the patch target dir |
| `revision_type` | yes | `REPLACE` \| `CELL_PATCH` \| `CELL_CREATE` \| `FILE_CREATE` \| `FILE_MOVE` |
| `cell_id` | for CELL_PATCH | Must match an existing `# %% [<cell_id>]` marker. For paired cells, target the `:start` marker |
| `code_content` | for patching | For CELL_PATCH: the **complete cell**, beginning with its `:start` marker and including the matching `:end` marker — never a partial diff. For REPLACE / FILE_CREATE: **plain code only** — no markers, no schema header (annotation is applied automatically after a successful patch) |
| `new_filename` | for FILE_MOVE | The destination path |
| `append_after` | optional, CELL_CREATE | An existing cell_id (e.g. `func:top:end`, `method:Cls.run:end`); the new code is inserted immediately after that cell. A `:start` id resolves to its matching `:end`. Omitted: inserts before `module:main_guard`, or at EOF if there is none |

Tool selection (the user pays per token — pick the laconic one):
- `REPLACE` — total rewrites or files under ~50 lines (plain code, no markers)
- `CELL_PATCH` — surgical updates to a specific function/class/method
- `CELL_CREATE` — add new logic to an annotated file (plain code, no markers; place with `append_after`)
- `FILE_CREATE` — brand new file from scratch (plain code, no markers)
- `FILE_MOVE` — rename or move a file

### Placement, classes, and nesting

- **Adding a method to a class:** use CELL_CREATE with `append_after` set to
  an existing method of that class (e.g. `"method:UserService.validate:end"`)
  and emit the code **already indented for the class body**. Markers are
  generated automatically after apply.
- **Nested (inner) functions have no markers** and are never patchable on
  their own — they are part of their parent function's cell. To change one,
  CELL_PATCH the parent and emit the parent's complete cell.
- **Prefer nested functions only as a last resort** (true closures over local
  state). When writing new code, default to module-level functions or private
  methods — they get their own cells and stay cheaply patchable. If you
  encounter an existing nested function that doesn't need to be one, propose
  extracting it to the user **once**; if the user declines, respect that
  choice and don't raise it again.
- **`__init__` is a normal method cell:** `method:Cls.__init__:start`.
- Indentation is your responsibility; if it's wrong the file won't parse and
  the patch report will tell you (`Post-check FAILED`), with the previous
  version safe in a backup.

### The patch report

`cellsmith patch` prints a numbered, ordered report — one line per artifact /
revision, `OK` or `FAILED`, plus a corrective instruction for each failure.
Exit codes: `0` all applied, `2` changelog gate rejected (nothing written),
`3` partial — some operations failed.

**If some revisions fail, re-emit ONLY the FAILED ones.** The OK ones are
already applied; re-sending them wastes tokens and can double-apply appends.
After a successful patch the tool automatically strips and re-annotates every
touched `.py` file, so markers are always in perfect alignment — you never
need to maintain them by hand.

### `changelog[]` — **BLOCKING GATE**

Every patch payload **must** include at least one changelog entry. Payloads
without a valid `changelog` are rejected by `cellsmith patch` (exit code 2)
before any disk writes happen. Accepted entries are appended to
`CHANGELOG.cellsmith.jsonl` at the patch target root.

| Field | Required | Notes |
|---|---|---|
| `change_type` | yes | One of: `new_feature`, `correcting_implementation`, `bug_fix`, `refactor`, `schema_migration` |
| `summary` | yes | One concise affirmative sentence describing the **final state achieved** — not a recount of past mistakes |
| `details` | no | Array of strings, granular technical bullets |
| `timestamp` | no | ISO-8601 UTC; `cellsmith patch` fills it in at apply time if omitted |
| `author` | no | Free-form model/agent identifier |

## Step 3 — hand off the JSON

Save the JSON as `patch.json` (or any name) and let the user (or your shell
tool) run:

```bash
cellsmith patch patch.json .
```

If the file changed and the diff isn't what you wanted, roll back:

```bash
cellsmith rollback patch.json .
```

---

*This file is auto-generated by `cellsmith annotate-agent` and regenerated
on every run. Don't edit by hand.*
"""


# %% [class:CellAnnotator:start]
class CellAnnotator(ast.NodeVisitor):
# %% [method:CellAnnotator.__init__:start]
    def __init__(self):
        self.insertions: List[Tuple[int, str]] = []
        self.current_class: str = ""
        self.imports_marked: bool = False
        self.function_depth: int = 0
# %% [method:CellAnnotator.__init__:end]

# %% [method:CellAnnotator._handle_import:start]
    def _handle_import(self, node: ast.AST) -> None:
        if not self.imports_marked and getattr(node, 'col_offset', -1) == 0:
            self.insertions.append((node.lineno, "\n# %% [imports]\n"))
            self.imports_marked = True
        self.generic_visit(node)
# %% [method:CellAnnotator._handle_import:end]

# %% [method:CellAnnotator.visit_Import:start]
    def visit_Import(self, node: ast.Import) -> None:
        self._handle_import(node)
# %% [method:CellAnnotator.visit_Import:end]

# %% [method:CellAnnotator.visit_ImportFrom:start]
    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self._handle_import(node)
# %% [method:CellAnnotator.visit_ImportFrom:end]

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
        # Only annotate module-level functions and class-direct methods.
        # Nested (closure) functions live inside their parent's cell.
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
        """Mark clusters of module-level code that aren't imports/funcs/classes.

        These would otherwise be swallowed by a CELL_PATCH on the preceding
        cell (which scans forward until the next marker — or EOF if there is
        none).  We emit:
          # %% [module:main_guard]  for `if __name__ == "__main__":` blocks
          # %% [module:init]        for everything else (instantiations, etc.)
        Multiple non-contiguous clusters of the latter get numbered:
          module:init, module:init:2, module:init:3 …
        """
        self.generic_visit(node)

        SKIP = (
            ast.Import, ast.ImportFrom,
            ast.FunctionDef, ast.AsyncFunctionDef,
            ast.ClassDef,
        )

        init_count = 0
        in_group = False

        for stmt in node.body:
            if isinstance(stmt, SKIP):
                in_group = False
                continue

            # `if __name__ == "__main__":` always gets its own cell, even if
            # we're already inside a module:init group.
            is_main_guard = (
                isinstance(stmt, ast.If)
                and isinstance(stmt.test, ast.Compare)
                and isinstance(stmt.test.left, ast.Name)
                and stmt.test.left.id == "__name__"
                and any(isinstance(op, ast.Eq) for op in stmt.test.ops)
            )
            if is_main_guard:
                self.insertions.append((stmt.lineno, "# %% [module:main_guard]\n"))
                in_group = False  # guard ends the current init group
                continue

            # Everything else: group consecutive runs under module:init[:N]
            if not in_group:
                in_group = True
                init_count += 1
                suffix = f":{init_count}" if init_count > 1 else ""
                self.insertions.append((stmt.lineno, f"# %% [module:init{suffix}]\n"))
# %% [method:CellAnnotator.visit_Module:end]
# %% [class:CellAnnotator:end]

# %% [func:annotate_file:start]
def annotate_file(filepath: Path, header: str = FULL_SCHEMA_HEADER) -> None:
    if not filepath.exists():
        logging.error(f"File not found: {filepath}")
        return

    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    source = "".join(lines)

    # 1. Identify custom 'knobs' ranges to protect them from inner annotation
    knob_ranges = []
    in_knob = False
    start_line = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("# %% [knobs:") and stripped.endswith(":start]"):
            in_knob = True
            start_line = i + 1
        elif in_knob and stripped.startswith("# %% [knobs:") and stripped.endswith(":end]"):
            knob_ranges.append((start_line, i + 1))
            in_knob = False

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        logging.error(f"Syntax error in {filepath}: {e}")
        return

    annotator = CellAnnotator()
    annotator.visit(tree)

    # 2. Filter out nodes inside a protected knob block
    valid_insertions = []
    for lineno, marker in annotator.insertions:
        inside_knob = any(start <= lineno <= end for start, end in knob_ranges)
        if not inside_knob:
            valid_insertions.append((lineno, marker))

    # Process bottom-to-top to prevent line shifting issues.
    # Secondary key: at the same line number, `:end` markers must land ABOVE
    # `:start` markers in the final file (a block's end precedes the next
    # block's start). With reverse sort + insert-at-same-index semantics, the
    # item processed LAST ends up on top — so `:start` (key 1) must sort
    # before `:end` (key 0) in the reversed order.
    valid_insertions.sort(
        key=lambda x: (x[0], 0 if ":end]" in x[1] else 1),
        reverse=True,
    )

    # Insert valid markers strictly if they don't already exist
    insert_count = 0
    for lineno, marker in valid_insertions:
        idx = lineno - 1
        already_annotated = False

        # Look upward from the target index (`:start` and simple markers
        # precede their code), skipping blanks and OTHER markers (adjacent
        # blocks stack end/start markers on consecutive lines) ...
        check_idx = idx - 1
        while check_idx >= 0:
            stripped = lines[check_idx].strip()
            if stripped == marker.strip():
                already_annotated = True
                break
            if not stripped or stripped.startswith("# %% ["):
                check_idx -= 1
                continue
            break  # Stop at the first non-blank, non-marker line

        # ... and downward from the target index (`:end` markers sit at the
        # line right after the block, i.e. exactly where we'd re-insert).
        if not already_annotated:
            check_idx = idx
            while check_idx < len(lines):
                stripped = lines[check_idx].strip()
                if stripped == marker.strip():
                    already_annotated = True
                    break
                if not stripped or stripped.startswith("# %% ["):
                    check_idx += 1
                    continue
                break

        if not already_annotated:
            lines.insert(idx, marker)
            insert_count += 1

    # 3. Prepend AI instructions block if neither schema variant exists yet
    already_has_header = any(
        "[ai_schema:instructions]" in line or "[ai_schema:pointer]" in line
        for line in lines[:20]
    )
    if not already_has_header:
        file_header = f"# filepath: {filepath.as_posix()}\n"
        lines.insert(0, file_header + header)
        insert_count += 1

    if insert_count == 0:
        logging.info(f"No new structures required annotation in {filepath}")
        return

    with open(filepath, "w", encoding="utf-8") as f:
        f.writelines(lines)

    logging.info(f"Annotated {filepath} with {insert_count} new elements.")
# %% [func:annotate_file:end]

# %% [func:create_backup:start]
def create_backup(filepath: Path) -> None:
    if filepath.exists():
        backup_idx = 1
        while filepath.with_suffix(f"{filepath.suffix}.bak.{backup_idx}").exists():
            backup_idx += 1

        for i in range(backup_idx - 1, 0, -1):
            old_bak = filepath.with_suffix(f"{filepath.suffix}.bak.{i}")
            new_bak = filepath.with_suffix(f"{filepath.suffix}.bak.{i+1}")
            shutil.move(old_bak, new_bak)

        backup_path = filepath.with_suffix(filepath.suffix + ".bak")
        if backup_path.exists():
            shutil.move(backup_path, filepath.with_suffix(filepath.suffix + ".bak.1"))

        shutil.copy2(filepath, backup_path)
        logging.info(f"Created versioned backup: {backup_path}")
# %% [func:create_backup:end]

# %% [func:_validate_changelog:start]
def _validate_changelog(entries: list) -> list:
    """Validate changelog entries from a patch payload. Raise ValueError on any issue.

    Returns the normalized list (timestamp filled where missing, in UTC).
    """
    if not isinstance(entries, list) or not entries:
        raise ValueError(
            "patch payload missing required `changelog` array (must contain at "
            "least one entry with change_type + summary)."
        )

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    normalized = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"changelog[{i}] is not an object")
        change_type = entry.get("change_type")
        summary = entry.get("summary")
        if change_type not in VALID_CHANGE_TYPES:
            raise ValueError(
                f"changelog[{i}].change_type must be one of "
                f"{sorted(VALID_CHANGE_TYPES)}; got {change_type!r}"
            )
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError(f"changelog[{i}].summary must be a non-empty string")
        normalized.append({
            "timestamp": entry.get("timestamp") or now,
            "author": entry.get("author"),
            "change_type": change_type,
            "summary": summary.strip(),
            "details": entry.get("details") or [],
        })
    return normalized
# %% [func:_validate_changelog:end]


# %% [func:write_skill_doc:start]
def write_skill_doc(project_root: Path) -> Path:
    """Write the markdown skill doc at the project root. Always overwrites
    (single source-of-truth: SKILL_DOC_MARKDOWN). Returns the path written."""
    project_root.mkdir(parents=True, exist_ok=True)
    path = project_root / SKILL_DOC_FILENAME
    path.write_text(SKILL_DOC_MARKDOWN, encoding="utf-8")
    return path
# %% [func:write_skill_doc:end]


# %% [func:_write_changelog:start]
def _write_changelog(entries: list, target_dir: Path) -> None:
    """Append validated entries (one JSON object per line) to the project changelog."""
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / CHANGELOG_FILE
    with open(path, "a", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
    logging.info(f"Recorded {len(entries)} changelog entry(s) in {path}")
# %% [func:_write_changelog:end]


# %% [func:_detect_header:start]
def _detect_header(filepath: Path, target_dir: Path) -> str:
    """Pick the schema header to use when re-annotating `filepath`.

    Prefer whatever variant the file already carries; for files with no
    header (fresh REPLACE / FILE_CREATE payloads), use the pointer header
    if the project has a skill doc at the target root, else the full header.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for _ in range(20):
                line = f.readline()
                if not line:
                    break
                if "[ai_schema:pointer]" in line:
                    return POINTER_HEADER
                if "[ai_schema:instructions]" in line:
                    return FULL_SCHEMA_HEADER
    except OSError:
        pass
    if (target_dir / SKILL_DOC_FILENAME).exists():
        return POINTER_HEADER
    return FULL_SCHEMA_HEADER
# %% [func:_detect_header:end]


# %% [func:apply_revisions:start]
def apply_revisions(data: dict, target_dir: Path) -> bool:
    """Apply a patch payload.

    Prints an ordered, numbered per-operation report (OK / FAILED with a
    corrective instruction) so an agent can re-emit only what failed.
    After applying, every touched .py file is stripped and re-annotated so
    markers stay in perfect alignment regardless of what the payload
    contained. Returns True only if every operation succeeded.
    """
    changelog_entries = _validate_changelog(data.get("changelog"))
    backed_up_files = set()
    results: List[Tuple[bool, str]] = []
    touched_py: dict = {}  # Path -> header to re-apply

    def _ensure_backup(filepath: Path):
        if filepath not in backed_up_files:
            create_backup(filepath)
            backed_up_files.add(filepath)

    def _ok(label: str, message: str) -> None:
        results.append((True, f"{label} OK - {message}"))

    def _fail(label: str, message: str) -> None:
        results.append((False, f"{label} FAILED - {message}"))

    def _touch(filepath: Path) -> None:
        # Must run BEFORE the write so header detection sees the old file.
        if filepath.suffix == ".py" and filepath not in touched_py:
            touched_py[filepath] = _detect_header(filepath, target_dir)

    artifacts = data.get("artifacts", [])
    for n, artifact in enumerate(artifacts, 1):
        label = f"Artifact [{n}]"
        filename = artifact.get("filename")
        code = artifact.get("code_content")
        if not filename or code is None:
            _fail(label, "artifact entries require `filename` and `code_content`.")
            continue
        target_file = target_dir / filename
        target_file.parent.mkdir(parents=True, exist_ok=True)
        if target_file.exists():
            # Back up so rollback restores rather than destroys.
            _ensure_backup(target_file)
        _touch(target_file)
        with open(target_file, "w", encoding="utf-8") as f:
            f.write(code)
        _ok(label, f"created {filename}")

    LABELS = {
        "CELL_PATCH": "Cell Patch",
        "REPLACE": "Replace",
        "FILE_CREATE": "File Create",
        "CELL_CREATE": "Cell Create",
        "FILE_MOVE": "File Move",
    }

    revisions = data.get("revisions", [])
    for n, rev in enumerate(revisions, 1):
        rev_type = rev.get("revision_type")
        label = f"{LABELS.get(rev_type, 'Revision')} [{n}]"
        filename = rev.get("filename")
        code = rev.get("code_content", "")

        if rev_type not in LABELS:
            _fail(label, f"unknown revision_type {rev_type!r}; use one of {sorted(LABELS)}.")
            continue
        if not filename:
            _fail(label, "missing `filename`.")
            continue
        target_file = target_dir / filename

        if rev_type == "FILE_MOVE":
            new_filename = rev.get("new_filename")
            if not new_filename:
                _fail(label, "FILE_MOVE requires a `new_filename` field with the destination path.")
                continue
            if not target_file.exists():
                _fail(label, f"cannot move {filename}: file does not exist. Check the path against the project tree.")
                continue
            new_target = target_dir / new_filename
            _ensure_backup(target_file)
            new_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(target_file, new_target)
            _ok(label, f"moved {filename} to {new_filename}")
            continue

        is_new_file = not target_file.exists()
        if is_new_file and rev_type not in ("REPLACE", "FILE_CREATE"):
            _fail(label, f"target file {filename} does not exist. Use FILE_CREATE to create new files.")
            continue

        if rev_type in ("REPLACE", "FILE_CREATE"):
            if not is_new_file:
                _ensure_backup(target_file)
            _touch(target_file)
            target_file.parent.mkdir(parents=True, exist_ok=True)
            with open(target_file, "w", encoding="utf-8") as f:
                f.write(code)
            _ok(label, f"{'created' if is_new_file else 'replaced'} {filename}")

        elif rev_type == "CELL_PATCH":
            cell_id = rev.get("cell_id")
            if not cell_id:
                _fail(label, "CELL_PATCH requires `cell_id` matching an existing marker (e.g. `func:name:start`).")
                continue

            marker = f"# %% [{cell_id}]"
            is_start_block = ":start]" in marker
            expected_end_marker = marker.replace(":start]", ":end]") if is_start_block else None

            # Payload validation: the complete cell must be emitted —
            # start marker first, matching end marker present.
            content_lines = [l.strip() for l in code.splitlines() if l.strip()]
            has_start = bool(content_lines) and content_lines[0] == marker
            has_end = (not is_start_block) or (expected_end_marker in content_lines)
            if not (has_start and has_end):
                _fail(label, (
                    "Ensure you are emitting the complete cell, not just changes or a "
                    "partial update — `code_content` must begin with the `:start` marker "
                    "line and include the matching `:end` marker."
                ))
                continue

            with open(target_file, "r", encoding="utf-8") as f:
                lines = f.readlines()

            start_idx = -1
            end_idx = -1
            for i, line in enumerate(lines):
                if line.strip() == marker:
                    start_idx = i
                    break
            if start_idx == -1:
                _fail(label, (
                    f"marker `{marker}` not found in {filename}. `cell_id` must exactly "
                    "match a marker in the CURRENT file — re-read the file skeleton if unsure."
                ))
                continue

            for i in range(start_idx + 1, len(lines)):
                stripped = lines[i].strip()
                if is_start_block:
                    if stripped == expected_end_marker:
                        end_idx = i + 1
                        break
                else:
                    if stripped.startswith("# %% ["):
                        end_idx = i
                        break
            if end_idx == -1:
                if is_start_block:
                    _fail(label, (
                        f"`{expected_end_marker}` is missing in {filename}, so the cell "
                        "boundary is broken. Re-emit the whole file via REPLACE to restore structure."
                    ))
                    continue
                end_idx = len(lines)  # simple (unpaired) cell at EOF

            if not code.endswith("\n"):
                code += "\n"
            new_lines = lines[:start_idx] + [code] + lines[end_idx:]
            _ensure_backup(target_file)
            _touch(target_file)
            with open(target_file, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
            _ok(label, f"patched {cell_id} in {filename}")

        elif rev_type == "CELL_CREATE":
            with open(target_file, "r", encoding="utf-8") as f:
                lines = f.readlines()

            insert_idx = len(lines)
            placement = "at end of file"
            append_after = rev.get("append_after")
            if append_after:
                anchor = f"# %% [{append_after}]"
                a_idx = -1
                for i, line in enumerate(lines):
                    if line.strip() == anchor:
                        a_idx = i
                        break
                if a_idx == -1:
                    _fail(label, (
                        f"`append_after` marker `{anchor}` not found in {filename}. "
                        "Use an exact existing marker from the current file, "
                        "e.g. `func:name:end` or `method:Cls.name:end`."
                    ))
                    continue
                # If given a `:start` marker, resolve to the matching `:end`
                # so the new cell lands after the whole block.
                if ":start]" in anchor:
                    end_anchor = anchor.replace(":start]", ":end]")
                    for i in range(a_idx + 1, len(lines)):
                        if lines[i].strip() == end_anchor:
                            a_idx = i
                            break
                insert_idx = a_idx + 1
                placement = f"after {append_after}"
            else:
                # Default: land BEFORE the main guard, not after it.
                for i, line in enumerate(lines):
                    if line.strip() == "# %% [module:main_guard]":
                        insert_idx = i
                        placement = "before module:main_guard"
                        break

            if not code.endswith("\n"):
                code += "\n"
            new_lines = lines[:insert_idx] + ["\n" + code] + lines[insert_idx:]
            _ensure_backup(target_file)
            _touch(target_file)
            with open(target_file, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
            _ok(label, f"inserted new cell in {filename} ({placement})")

    # Post-pass: normalize every touched .py file. Strip whatever markers /
    # headers the payload did or didn't include, then re-annotate from the
    # AST — guaranteeing perfect marker alignment. A file that no longer
    # parses is reported as a failure (its .bak still holds the pre-patch state).
    for filepath, header in touched_py.items():
        if not filepath.exists():
            continue
        label = f"Post-check [{filepath.name}]"
        try:
            ast.parse(filepath.read_text(encoding="utf-8"))
        except SyntaxError as e:
            _fail(label, (
                f"patched file no longer parses (line {e.lineno}: {e.msg}). Re-emit the "
                "affected cell completely via CELL_PATCH, or the whole file via REPLACE."
            ))
            continue
        strip_file(filepath, strip_prompt=True, strip_markers=True)
        annotate_file(filepath, header=header)

    # Ordered report: successes and failures exactly as encountered.
    for _, line in results:
        print(line)

    any_success = any(ok for ok, _ in results)
    all_ok = all(ok for ok, _ in results)
    if any_success or not results:
        _write_changelog(changelog_entries, target_dir)
    else:
        logging.warning("No operations applied; changelog not recorded.")
    return all_ok
# %% [func:apply_revisions:end]

# %% [func:strip_file:start]
def strip_file(
    filepath: Path,
    *,
    strip_prompt: bool = True,
    strip_markers: bool = True,
) -> int:
    """Remove the AI schema header and/or `# %% [...]` cell markers from a file.

    `# %% [knobs:...]` markers are always preserved — they delimit
    user-authored protected blocks, not CellSmith annotations.

    Returns the number of lines removed. No-ops on missing file.
    """
    if not filepath.exists():
        logging.warning(f"strip: file not found: {filepath}")
        return 0
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    out: List[str] = []
    skipping_schema = False
    schema_just_ended = False
    for line in lines:
        stripped = line.strip()

        if strip_prompt:
            if not out and stripped.startswith("# filepath:"):
                continue
            if stripped in ("# %% [ai_schema:instructions]", "# %% [ai_schema:pointer]"):
                skipping_schema = True
                continue
            if skipping_schema:
                if stripped == "# %% [ai_schema:end]":
                    skipping_schema = False
                    schema_just_ended = True
                continue
            # Drop the trailing instruction comment block that follows ai_schema:end
            # (it's prepended in annotate but not enclosed by markers).
            if schema_just_ended:
                if stripped == "" or stripped.startswith("#"):
                    continue
                schema_just_ended = False

        if (
            strip_markers
            and stripped.startswith("# %% [")
            and stripped.endswith("]")
            and not stripped.startswith("# %% [knobs:")
        ):
            continue

        out.append(line)

    removed = len(lines) - len(out)
    if removed:
        with open(filepath, "w", encoding="utf-8") as f:
            f.writelines(out)
    return removed
# %% [func:strip_file:end]

# %% [func:_load_gitignore:start]
def _load_gitignore(root: Path):
    """Return a pathspec.PathSpec built from <root>/.gitignore, or None."""
    gi = root / ".gitignore"
    if not gi.exists():
        return None
    try:
        import pathspec
    except ImportError:
        logging.warning("pathspec not installed; skipping .gitignore filtering")
        return None
    with open(gi, "r", encoding="utf-8") as f:
        return pathspec.PathSpec.from_lines("gitwildmatch", f.readlines())
# %% [func:_load_gitignore:end]


# %% [func:iter_python_files:start]
def iter_python_files(
    target: Path,
    *,
    use_gitignore: bool = True,
    include_hidden: bool = False,
) -> List[Path]:
    """Walk `target`, yielding Python files to annotate.

    Skips dotted dirs/files (.git, .venv, ...) and dunder dirs (__pycache__, ...)
    by default, and honors the nearest .gitignore at `target` if present.
    """
    if target.is_file():
        return [target] if target.suffix == ".py" else []

    spec = _load_gitignore(target) if use_gitignore else None
    results: List[Path] = []

    for path in target.rglob("*"):
        if path.is_dir():
            continue
        rel = path.relative_to(target)
        parts = rel.parts

        if not include_hidden and any(p.startswith(".") for p in parts):
            continue
        if any(p.startswith("__") and p.endswith("__") for p in parts[:-1]):
            continue
        if path.suffix != ".py":
            continue
        if spec is not None and spec.match_file(rel.as_posix()):
            continue

        results.append(path)
    return sorted(results)
# %% [func:iter_python_files:end]


# %% [func:rollback_revisions:start]
def rollback_revisions(data: dict, target_dir: Path) -> None:
    """Reverts changes applied by a JSON patch."""
    revisions = data.get("revisions", [])
    for rev in revisions:
        if rev.get("revision_type") == "FILE_MOVE":
            old_file = target_dir / rev["filename"]
            new_file = target_dir / rev.get("new_filename", "")
            if new_file.exists():
                old_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(new_file, old_file)
                logging.info(f"Reverted FILE_MOVE: {new_file} -> {old_file}")

    def _restore_from_backup(target_file: Path) -> bool:
        """Restore target_file from its .bak (rotating numbered backups down).
        Returns True if a backup existed and was restored."""
        backup_path = target_file.with_suffix(target_file.suffix + ".bak")
        if not backup_path.exists():
            return False
        shutil.copy2(backup_path, target_file)
        backup_path.unlink()
        logging.info(f"Restored file from backup (rollback): {target_file}")

        idx = 1
        while target_file.with_suffix(f"{target_file.suffix}.bak.{idx}").exists():
            old_bak = target_file.with_suffix(f"{target_file.suffix}.bak.{idx}")
            if idx == 1:
                new_bak = target_file.with_suffix(target_file.suffix + ".bak")
            else:
                new_bak = target_file.with_suffix(f"{target_file.suffix}.bak.{idx-1}")
            shutil.move(old_bak, new_bak)
            idx += 1
        return True

    artifacts = data.get("artifacts", [])
    for artifact in artifacts:
        target_file = target_dir / artifact["filename"]
        # If the artifact overwrote a pre-existing file, a backup was taken
        # at patch time — restore it. Otherwise the artifact was net-new:
        # delete it.
        if _restore_from_backup(target_file):
            continue
        if target_file.exists():
            target_file.unlink()
            logging.info(f"Removed created artifact (rollback): {target_file}")

    for rev in revisions:
        rev_type = rev.get("revision_type")
        if rev_type == "FILE_MOVE":
            continue

        target_file = target_dir / rev["filename"]

        if _restore_from_backup(target_file):
            continue

        # No backup: the revision created this file from scratch
        # (FILE_CREATE, or REPLACE on a previously missing path). Undo by
        # deleting it. CELL_PATCH/CELL_CREATE always back up first, so a
        # missing backup for those means there's nothing to undo.
        if rev_type in ("FILE_CREATE", "REPLACE") and target_file.exists():
            target_file.unlink()
            logging.info(f"Removed created file (rollback): {target_file}")
# %% [func:rollback_revisions:end]

# %% [func:main:start]
def main() -> None:
    parser = argparse.ArgumentParser(description="AST-based Code Annotator and JSON Patcher")
    subparsers = parser.add_subparsers(dest="command", required=True)

    annotate_parser = subparsers.add_parser("annotate", help="Annotate Python file(s) with cell markers + full schema header")
    annotate_parser.add_argument("target", type=Path, help="Target Python file or directory")
    annotate_parser.add_argument("--no-gitignore", action="store_true", help="Don't filter via .gitignore")
    annotate_parser.add_argument("--include-hidden", action="store_true", help="Include dotted (hidden) dirs/files")
    annotate_parser.add_argument("--dry-run", action="store_true", help="List files that would be annotated, don't write")

    agent_parser = subparsers.add_parser(
        "annotate-agent",
        help=f"Like annotate, but uses a laconic pointer header and writes {SKILL_DOC_FILENAME} at the project root",
    )
    agent_parser.add_argument("target", type=Path, help="Target Python file or directory")
    agent_parser.add_argument("--no-gitignore", action="store_true", help="Don't filter via .gitignore")
    agent_parser.add_argument("--include-hidden", action="store_true", help="Include dotted (hidden) dirs/files")
    agent_parser.add_argument("--dry-run", action="store_true", help="List files that would be annotated, don't write")
    agent_parser.add_argument(
        "--skill-root", type=Path, default=None,
        help=f"Where to write {SKILL_DOC_FILENAME} (default: target if dir, else target's parent)",
    )

    subparsers.add_parser("status", help="Report whether cellsmith is installed and runnable (for agent probes)")

    patch_parser = subparsers.add_parser("patch", help="Apply JSON response patch to target directory")
    patch_parser.add_argument("json_file", type=Path, help="JSON response file")
    patch_parser.add_argument("target_dir", type=Path, default=Path("."), nargs="?", help="Root directory for patching")

    strip_parser = subparsers.add_parser("strip", help="Remove cell markers and/or the AI schema prompt header")
    strip_parser.add_argument("target", type=Path, help="Target Python file or directory")
    strip_parser.add_argument("--prompt-only", action="store_true", help="Only strip the AI schema prompt header")
    strip_parser.add_argument("--markers-only", action="store_true", help="Only strip the # %% cell markers")
    strip_parser.add_argument("--no-gitignore", action="store_true", help="Don't filter via .gitignore (dir mode)")
    strip_parser.add_argument("--include-hidden", action="store_true", help="Include dotted dirs/files (dir mode)")
    strip_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")

    rollback_parser = subparsers.add_parser("rollback", help="Rollback changes applied by a JSON patch")
    rollback_parser.add_argument("json_file", type=Path, help="JSON response file used for patching")
    rollback_parser.add_argument("target_dir", type=Path, default=Path("."), nargs="?", help="Root directory for patching")

    args = parser.parse_args()

    if args.command == "status":
        # Stable, parseable single-line output for agent probes.
        print(f"available cellsmith {__version__}")
        return
    if args.command in ("annotate", "annotate-agent"):
        if not args.target.exists():
            logging.error(f"Target does not exist: {args.target}")
            sys.exit(1)
        files = iter_python_files(
            args.target,
            use_gitignore=not args.no_gitignore,
            include_hidden=args.include_hidden,
        )
        if not files:
            logging.warning(f"No Python files found under {args.target}")
            return
        if args.dry_run:
            for f in files:
                print(f)
            logging.info(f"[dry-run] {len(files)} file(s) would be annotated")
            return
        header = POINTER_HEADER if args.command == "annotate-agent" else FULL_SCHEMA_HEADER
        for f in files:
            annotate_file(f, header=header)
        if args.command == "annotate-agent":
            skill_root = args.skill_root
            if skill_root is None:
                skill_root = args.target if args.target.is_dir() else args.target.parent
            written = write_skill_doc(skill_root)
            logging.info(f"Wrote skill doc to {written}")
        logging.info(f"Processed {len(files)} file(s)")
    elif args.command == "strip":
        if not args.target.exists():
            logging.error(f"Target does not exist: {args.target}")
            sys.exit(1)
        files = iter_python_files(
            args.target,
            use_gitignore=not args.no_gitignore,
            include_hidden=args.include_hidden,
        )
        if not files:
            logging.warning(f"No Python files found under {args.target}")
            return
        strip_prompt = not args.markers_only
        strip_markers = not args.prompt_only
        what = []
        if strip_prompt:
            what.append("AI schema prompt header")
        if strip_markers:
            what.append("# %% cell markers")
        scope = ", ".join(what) if what else "(nothing)"
        if not args.yes:
            print(f"About to strip {scope} from {len(files)} file(s) under {args.target}.")
            print("This is reversible with `cellsmith annotate` but will modify files in-place.")
            ans = input("Proceed? [y/N] ").strip().lower()
            if ans not in ("y", "yes"):
                logging.info("Aborted.")
                return
        total = 0
        for f in files:
            total += strip_file(f, strip_prompt=strip_prompt, strip_markers=strip_markers)
        logging.info(f"Stripped {total} line(s) across {len(files)} file(s)")
    elif args.command in ["patch", "rollback"]:
        if not args.json_file.exists():
            logging.error(f"JSON file not found: {args.json_file}")
            sys.exit(1)

        with open(args.json_file, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError as e:
                logging.error(f"Invalid JSON: {e}")
                sys.exit(1)

        if args.command == "patch":
            try:
                all_ok = apply_revisions(data, args.target_dir)
            except ValueError as e:
                logging.error(f"patch rejected: {e}")
                sys.exit(2)
            if not all_ok:
                sys.exit(3)
        elif args.command == "rollback":
            rollback_revisions(data, args.target_dir)
# %% [func:main:end]

# %% [module:main_guard]
if __name__ == "__main__":
    main()
