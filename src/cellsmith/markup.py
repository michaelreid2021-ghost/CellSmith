# %% [ai_schema:instructions]
# AI INSTRUCTIONS - PATCH SCHEMA:
#
# To modify this file, return a JSON response with the following structure.
# When using CELL_PATCH, `cell_id` MUST be an exact marker that exists in the file.
#
# {
#   "revisions": [
#     {
#       "filename": "path/to/this/file.py",
#       "revision_type": "CELL_PATCH",  # Or "REPLACE", "CELL_CREATE"
#       "cell_id": "func:my_function",  # Match an exact marker (e.g. 'imports', 'func:x', 'method:Cls.x', 'module:init', 'module:main_guard')
#       "code_content": "# %% [func:my_function]\ndef my_function():\n    pass\n"
#     }
#   ],
#   "changelog": [
#     {
#       "change_type": "bug_fix",   # Required. One of: new_feature, correcting_implementation, bug_fix, refactor, schema_migration
#       "summary": "Concise single-sentence description of the final state achieved.",
#       "details": ["Optional bullet of granular technical change."]
#     }
#   ]
# }
#
# BLOCKING GATE: every patch response MUST include at least one `changelog`
# entry. Classify your work strictly using the `change_type` enum. Write the
# `summary` as affirmative documentation of the final state — not a recount
# of past conversational errors.
#
# Choose the most efficient tool for the job (the user pays per token):
#   * REPLACE     : For new files, total rewrites, or files under 50 lines.
#   * CELL_PATCH  : For surgical updates to a specific function/class/method.
#                   `cell_id` MUST exist in the current SKELETON of the file.
#   * CELL_CREATE : To append new logic. Use `insert_after` to place it.
# %% [ai_schema:end]


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
    "#\n"
    "# {\n"
    "#   \"revisions\": [\n"
    "#     {\n"
    "#       \"filename\": \"path/to/this/file.py\",\n"
    "#       \"revision_type\": \"CELL_PATCH\",  # Or \"REPLACE\", \"CELL_CREATE\"\n"
    "#       \"cell_id\": \"func:my_function\",  # Match an exact marker (e.g. 'imports', 'func:x', 'method:Cls.x', 'module:init', 'module:main_guard')\n"
    "#       \"code_content\": \"# %% [func:my_function]\\ndef my_function():\\n    pass\\n\"\n"
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
    "#   * REPLACE     : For new files, total rewrites, or files under 50 lines.\n"
    "#   * CELL_PATCH  : For surgical updates to a specific function/class/method.\n"
    "#                   `cell_id` MUST exist in the current SKELETON of the file.\n"
    "#   * CELL_CREATE : To append new logic. Use `insert_after` to place it.\n"
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
SKILL_DOC_MARKDOWN = """# CellSmith patch schema

This project's `.py` files have been annotated by **CellSmith** with cell
markers (`# %% [func:name]`, `# %% [class:Foo]`, `# %% [method:Cls.x]`,
`# %% [imports]`, etc.). Each annotated file opens with a short pointer to
this document.

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
      "cell_id": "method:UserService.validate",
      "code_content": "# %% [method:UserService.validate]\\n    def validate(self, ...):\\n        ..."
    }
  ],
  "artifacts": [
    {
      "filename": "new_helper.py",
      "code_content": "# full source of the new file"
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

### `revisions[]` — surgical edits to existing files

| Field | Required | Notes |
|---|---|---|
| `filename` | yes | Path relative to the patch target dir |
| `revision_type` | yes | `REPLACE` \\| `CELL_PATCH` \\| `CELL_CREATE` |
| `cell_id` | for CELL_PATCH | Must match an existing `# %% [<cell_id>]` marker in the file |
| `code_content` | yes | First line of CELL_PATCH/CREATE must be the marker. Provide full logic — never redact for brevity. |

Tool selection (the user pays per token — pick the laconic one):
- `REPLACE` — new files, total rewrites, or files under ~50 lines
- `CELL_PATCH` — surgical updates to a specific function/class/method (`cell_id` must already exist)
- `CELL_CREATE` — append new logic to an annotated file

### `artifacts[]` — brand-new files

Use for files that don't exist yet. Provide `filename` and the full
`code_content`.

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

# %% [class:CellAnnotator]
class CellAnnotator(ast.NodeVisitor):
# %% [method:CellAnnotator.__init__]
    def __init__(self):
        self.insertions: List[Tuple[int, str]] = []
        self.current_class: str = ""
        self.imports_marked: bool = False

# %% [method:CellAnnotator._handle_import]
    def _handle_import(self, node: ast.AST) -> None:
        if not self.imports_marked and getattr(node, 'col_offset', -1) == 0:
            self.insertions.append((node.lineno, "\n# %% [imports]\n"))
            self.imports_marked = True
        self.generic_visit(node)

# %% [method:CellAnnotator.visit_Import]
    def visit_Import(self, node: ast.Import) -> None:
        self._handle_import(node)

# %% [method:CellAnnotator.visit_ImportFrom]
    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self._handle_import(node)

# %% [method:CellAnnotator.visit_ClassDef]
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        base_id = f"class:{node.name}"
        self.insertions.append((node.lineno, f"# %% [{base_id}:start]\n"))
        if hasattr(node, "end_lineno") and node.end_lineno:
            self.insertions.append((node.end_lineno + 1, f"# %% [{base_id}:end]\n"))
        previous_class = self.current_class
        self.current_class = node.name
        self.generic_visit(node)
        self.current_class = previous_class
# %% [method:CellAnnotator.visit_FunctionDef]
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if self.current_class:
            base_id = f"method:{self.current_class}.{node.name}"
        else:
            base_id = f"func:{node.name}"
        self.insertions.append((node.lineno, f"# %% [{base_id}:start]\n"))
        if hasattr(node, "end_lineno") and node.end_lineno:
            self.insertions.append((node.end_lineno + 1, f"# %% [{base_id}:end]\n"))
        self.generic_visit(node)
# %% [method:CellAnnotator.visit_AsyncFunctionDef]
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

# %% [method:CellAnnotator.visit_Module]
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

# %% [func:annotate_file]
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

    # Process bottom-to-top to prevent line shifting issues
    valid_insertions.sort(key=lambda x: x[0], reverse=True)

    # Insert valid markers strictly if they don't already exist
    insert_count = 0
    for lineno, marker in valid_insertions:
        idx = lineno - 1
        already_annotated = False
        
        # Look strictly upward from the target index
        check_idx = idx - 1
        while check_idx >= 0:
            stripped = lines[check_idx].strip()
            if not stripped:  # Skip empty lines to find the true preceding line
                check_idx -= 1
                continue
            if stripped == marker.strip():
                already_annotated = True
            break  # Stop checking at the first non-empty line
            
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
# %% [func:create_backup]
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
# %% [func:_validate_changelog]
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


# %% [func:write_skill_doc]
def write_skill_doc(project_root: Path) -> Path:
    """Write the markdown skill doc at the project root. Always overwrites
    (single source-of-truth: SKILL_DOC_MARKDOWN). Returns the path written."""
    project_root.mkdir(parents=True, exist_ok=True)
    path = project_root / SKILL_DOC_FILENAME
    path.write_text(SKILL_DOC_MARKDOWN, encoding="utf-8")
    return path


# %% [func:_write_changelog]
def _write_changelog(entries: list, target_dir: Path) -> None:
    """Append validated entries (one JSON object per line) to the project changelog."""
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / CHANGELOG_FILE
    with open(path, "a", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
    logging.info(f"Recorded {len(entries)} changelog entry(s) in {path}")


# %% [func:apply_revisions]
def apply_revisions(data: dict, target_dir: Path) -> None:
    changelog_entries = _validate_changelog(data.get("changelog"))
    backed_up_files = set()

# %% [func:_ensure_backup]
    def _ensure_backup(filepath: Path):
        if filepath not in backed_up_files:
            create_backup(filepath)
            backed_up_files.add(filepath)

    artifacts = data.get("artifacts", [])
    for artifact in artifacts:
        target_file = target_dir / artifact["filename"]
        target_file.parent.mkdir(parents=True, exist_ok=True)
        
        code = artifact["code_content"]
        if not code.startswith("# %% [ai_schema"):
            code = POINTER_HEADER + code
            
        with open(target_file, "w", encoding="utf-8") as f:
            f.write(code)
        logging.info(f"Created artifact: {target_file}")

    revisions = data.get("revisions", [])
    for rev in revisions:
        target_file = target_dir / rev["filename"]
        rev_type = rev.get("revision_type")
        code = rev.get("code_content", "")

        if rev_type == "FILE_MOVE":
            new_filename = rev.get("new_filename")
            if not new_filename:
                logging.error(f"FILE_MOVE missing 'new_filename' for {target_file}")
                continue
            new_target = target_dir / new_filename
            if not target_file.exists():
                logging.warning(f"Target missing for FILE_MOVE: {target_file}")
                continue
            _ensure_backup(target_file)
            new_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(target_file, new_target)
            logging.info(f"Moved {target_file} to {new_target}")
            continue

        is_new_file = not target_file.exists()
        if is_new_file and rev_type != "REPLACE":
            logging.warning(f"Target file missing for revision, skipping: {target_file}")
            continue

        if not is_new_file:
            _ensure_backup(target_file)

        if rev_type == "REPLACE":
            if is_new_file and not code.startswith("# %% [ai_schema"):
                code = POINTER_HEADER + code
            target_file.parent.mkdir(parents=True, exist_ok=True)
            with open(target_file, "w", encoding="utf-8") as f:
                f.write(code)
            logging.info(f"Replaced entire file: {target_file}")

        elif rev_type == "CELL_PATCH":
            cell_id = rev.get("cell_id")
            if not cell_id:
                logging.error(f"CELL_PATCH missing cell_id for {target_file}")
                continue

            marker = f"# %% [{cell_id}]"
            with open(target_file, "r", encoding="utf-8") as f:
                lines = f.readlines()

            start_idx = -1
            end_idx = len(lines)

            for i, line in enumerate(lines):
                if line.strip() == marker:
                    start_idx = i
                    break

            if start_idx == -1:
                logging.error(f"Marker {marker} not found in {target_file}")
                continue

            is_start_block = ":start]" in marker
            expected_end_marker = marker.replace(":start]", ":end]") if is_start_block else None

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

            if not code.endswith("\n"):
                code += "\n"

            new_lines = lines[:start_idx] + [code] + lines[end_idx:]

            with open(target_file, "w", encoding="utf-8") as f:
                f.writelines(new_lines)

            logging.info(f"Patched cell {cell_id} in {target_file}")

        elif rev_type == "CELL_CREATE":
            with open(target_file, "a", encoding="utf-8") as f:
                f.write("\n" + code + "\n")
            logging.info(f"Appended new cell to {target_file}")

    _write_changelog(changelog_entries, target_dir)
# %% [func:strip_file]
def strip_file(
    filepath: Path,
    *,
    strip_prompt: bool = True,
    strip_markers: bool = True,
) -> int:
    """Remove the AI schema header and/or `# %% [...]` cell markers from a file.

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

        if strip_markers and stripped.startswith("# %% [") and stripped.endswith("]"):
            continue

        out.append(line)

    removed = len(lines) - len(out)
    if removed:
        with open(filepath, "w", encoding="utf-8") as f:
            f.writelines(out)
    return removed
# %% [func:_load_gitignore]
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


# %% [func:iter_python_files]
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


# %% [func:rollback_revisions]
def rollback_revisions(data: dict, target_dir: Path) -> None:
    """Reverts changes applied by a JSON patch."""
    revisions = data.get("revisions", [])
    for rev in revisions:
        if rev.get("revision_type") == "FILE_MOVE":
            old_file = target_dir / rev["filename"]
            new_file = target_dir / rev.get("new_filename", "")
            if new_file.exists():
                new_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(new_file, old_file)
                logging.info(f"Reverted FILE_MOVE: {new_file} -> {old_file}")

    artifacts = data.get("artifacts", [])
    for artifact in artifacts:
        target_file = target_dir / artifact["filename"]
        if target_file.exists():
            target_file.unlink()
            logging.info(f"Removed created artifact (rollback): {target_file}")

    for rev in revisions:
        if rev.get("revision_type") == "FILE_MOVE":
            continue
            
        target_file = target_dir / rev["filename"]
        backup_path = target_file.with_suffix(target_file.suffix + ".bak")
        
        if backup_path.exists():
            shutil.copy2(backup_path, target_file)
            backup_path.unlink()
            logging.info(f"Restored file from backup (rollback): {target_file}")
            
            idx = 1
            while target_file.with_suffix(f"{target_file.suffix}.bak.{idx}").exists():
                old_bak = target_file.with_suffix(f"{target_file.suffix}.bak.{idx}")
                new_bak = target_file.with_suffix(target_file.suffix + ".bak") if idx == 1 else target_file.with_suffix(f"{target_file.suffix}.bak.{idx-1}")
                shutil.move(old_bak, new_bak)
                idx += 1
        else:
            pass
# %% [func:main]
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

    # New Rollback Parser
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
                apply_revisions(data, args.target_dir)
            except ValueError as e:
                logging.error(f"patch rejected: {e}")
                sys.exit(2)
        elif args.command == "rollback":
            rollback_revisions(data, args.target_dir)

# %% [module:main_guard]
if __name__ == "__main__":
    main()
