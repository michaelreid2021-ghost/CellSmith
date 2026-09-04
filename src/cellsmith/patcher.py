# filepath: src/cellsmith/patcher.py
# %% [ai_schema:pointer]
# CellSmith-annotated file. Cells marked with `# %% [<cell_id>]`.
# To modify safely: load `CELLSMITH_PATCH_SCHEMA.md` at the project root
# for the full JSON patch schema (incl. required `changelog` block).
# Run `cellsmith status` first — if it errors, ignore these markers and
# edit the file directly per the user's request.
# %% [ai_schema:end]

# %% [module:init:start]
"""The patch engine: changelog gate, revision application, and rollback.

`apply_revisions` is the only writer of user code; every operation it performs
is backed up first so `rollback_revisions` can undo the whole payload.
"""
# %% [module:init:end]

# %% [imports:start]
import ast
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from cellsmith.annotator import annotate_file, plan_insertions
from cellsmith.constants import (
    CHANGELOG_FILE,
    FULL_SCHEMA_HEADER,
    POINTER_HEADER,
    SKILL_DOC_FILENAME,
    SKILL_DOC_MARKDOWN,
    VALID_CHANGE_TYPES,
)
from cellsmith.files import create_backup, strip_lines
from cellsmith.reader.schema import ReadRequest
from cellsmith.telemetry import ensure_runtime, instrument_file
from cellsmith.workspace import (
    backup_path,
    ensure_support_dirs,
    legacy_backup_path,
    restore_from_archive,
    store_in_archive,
    store_patch_file,
)
# %% [imports:end]


# %% [class:AmbiguousMarkerError:start]
class AmbiguousMarkerError(ValueError):
    """A payload targeted a `cell_id` that matches more than one marker.

    Raised before any disk write. `report` carries the rendered explanation,
    including a before/after view of every duplicated block so the caller can
    re-target the patch.
    """

# %% [method:AmbiguousMarkerError.__init__:start]
    def __init__(self, report: str):
        super().__init__("ambiguous destination")
        self.report = report
# %% [method:AmbiguousMarkerError.__init__:end]
# %% [class:AmbiguousMarkerError:end]


# %% [func:_marker_hits:start]
def _marker_hits(lines: List[str], marker_text: str) -> List[int]:
    """0-based indices of every line that is exactly `marker_text`."""
    return [i for i, line in enumerate(lines) if line.strip() == marker_text]
# %% [func:_marker_hits:end]


# %% [func:_cell_preview:start]
def _cell_preview(lines: List[str], start_idx: int, limit: int = 3) -> List[str]:
    """The first few non-blank code lines of the block opening at `start_idx`."""
    out = []
    for line in lines[start_idx + 1:]:
        stripped = line.strip()
        if stripped.startswith("# %% ["):
            break
        if not stripped:
            continue
        out.append(line.rstrip("\n"))
        if len(out) >= limit:
            break
    return out
# %% [func:_cell_preview:end]


# %% [func:_clean_cells:start]
def _clean_cells(lines: List[str], suffix: str) -> List[Tuple[str, List[str]]]:
    """`[(cell_id, code_lines)]` as a fresh strip + annotate would produce."""
    src = strip_lines(lines, strip_prompt=True, strip_markers=True)
    try:
        insertions = plan_insertions(src, suffix)
    except SyntaxError:
        return []

    starts, ends, order = {}, {}, []
    for lineno, marker in insertions:
        body = marker.strip()[len("# %% ["):-1]
        if body.endswith(":start"):
            cell_id = body[: -len(":start")]
            starts[cell_id] = lineno
            order.append(cell_id)
        elif body.endswith(":end"):
            ends[body[: -len(":end")]] = lineno

    cells = []
    for cell_id in order:
        start = starts[cell_id]
        end = ends.get(cell_id, start + 1)
        cells.append((cell_id, [l.rstrip("\n") for l in src[start - 1: end - 1]]))
    return cells
# %% [func:_clean_cells:end]


# %% [func:_clean_home:start]
def _clean_home(cells: List[Tuple[str, List[str]]], code_line: str) -> str:
    """The cell_id that `code_line` belongs to after a clean re-annotation."""
    needle = code_line.strip()
    for cell_id, body in cells:
        if any(line.strip() == needle for line in body):
            return cell_id
    return "(unknown)"
# %% [func:_clean_home:end]


# %% [func:_format_ambiguity:start]
def _format_ambiguity(
    filename: str, field: str, cell_id: str, hits: List[int],
    lines: List[str], suffix: str,
) -> str:
    """Render one ambiguous target as a before/after block."""
    clean = _clean_cells(lines, suffix)
    line_nos = ", ".join(str(i + 1) for i in hits)
    out = [
        f"  {filename}: {field} `{cell_id}` matches {len(hits)} markers "
        f"(lines {line_nos})."
    ]
    for n, idx in enumerate(hits, 1):
        preview = _cell_preview(lines, idx)
        home = _clean_home(clean, preview[0]) if preview else "(unknown)"
        out.append(f"    [{n}] line {idx + 1}  ->  after re-annotate: {home}")
        for line in preview:
            out.append(f"           {line}")
        if not preview:
            out.append("           (empty cell)")
    return "\n".join(out)
# %% [func:_format_ambiguity:end]


# %% [func:_check_ambiguity:start]
def _check_ambiguity(data: dict, target_dir: Path) -> None:
    """Reject a payload whose targets don't resolve to exactly one marker.

    `apply_revisions` resolves a `cell_id` by first match and then spans to the
    first matching `:end`, so a duplicated marker would silently replace the
    wrong — possibly larger — region. Rather than guess, refuse the whole
    payload before anything is written and show what the ids become once the
    file's markers are regenerated.
    """
    problems: List[str] = []

    for rev in data.get("revisions", []):
        rev_type = rev.get("revision_type")
        if rev_type not in ("CELL_PATCH", "CELL_CREATE"):
            continue
        filename = rev.get("filename")
        if not filename:
            continue
        target_file = target_dir / filename
        if not target_file.exists():
            continue

        lines = target_file.read_text(encoding="utf-8").splitlines(keepends=True)
        suffix = target_file.suffix

        if rev_type == "CELL_CREATE":
            anchor = rev.get("append_after")
            if not anchor:
                continue
            hits = _marker_hits(lines, f"# %% [{anchor}]")
            if len(hits) > 1:
                problems.append(
                    _format_ambiguity(filename, "append_after", anchor, hits, lines, suffix)
                )
            continue

        cell_id = rev.get("cell_id")
        if not cell_id:
            continue

        # Mirror apply_revisions' resolution: a bare id may match either the
        # paired `:start` form or a legacy single marker.
        if cell_id.endswith(":start") or cell_id.endswith(":end"):
            start_text = f"# %% [{cell_id}]"
            hits = _marker_hits(lines, start_text)
        else:
            start_text = f"# %% [{cell_id}:start]"
            hits = _marker_hits(lines, start_text) + _marker_hits(lines, f"# %% [{cell_id}]")
            hits.sort()

        if len(hits) > 1:
            problems.append(
                _format_ambiguity(filename, "cell_id", cell_id, hits, lines, suffix)
            )
            continue

        # A single start is still unusable if an orphan `:end` sits inside the
        # cell — the span stops at the first one found after the start.
        if len(hits) == 1 and start_text.endswith(":start]"):
            end_text = start_text.replace(":start]", ":end]")
            end_hits = [i for i in _marker_hits(lines, end_text) if i > hits[0]]
            if len(end_hits) > 1:
                problems.append(
                    _format_ambiguity(
                        filename, "cell_id", cell_id, [hits[0]] + end_hits, lines, suffix
                    )
                )

    if not problems:
        return

    raise AmbiguousMarkerError(
        "Patch NOT applied - ambiguous destination.\n\n"
        "These markers appear more than once, so the target cell is undecidable:\n\n"
        + "\n\n".join(problems)
        + "\n\nNothing was written. Markers are derived from the AST, so regenerate\n"
        "them and re-target the patch at the ids shown above:\n\n"
        "    cellsmith reannotate <file>\n"
    )
# %% [func:_check_ambiguity:end]




# %% [func:_instrument_patched:start]
def _instrument_patched(touched_files: dict, patched_cells: dict, target_dir: Path,
                        results: list) -> None:
    """Wrap the cells this payload patched in `@focal_trace`.

    Runs after re-annotation so the decorator lands above an accurate
    `:start` marker. Files that were replaced wholesale carry no recorded
    cell ids, so every top-level definition in them is instrumented.
    """
    python_files = [f for f in touched_files if f.suffix == ".py" and f.exists()]
    if not python_files:
        return
    ensure_runtime(target_dir)
    for filepath in python_files:
        cells = patched_cells.get(filepath)
        wrapped = instrument_file(filepath, cells if cells else None)
        if wrapped:
            results.append(
                (True, f"Telemetry [{filepath.name}] OK - instrumented {wrapped} cell(s)")
            )
# %% [func:_instrument_patched:end]


# %% [func:_run_post_patch_read:start]
def _run_post_patch_read(request: "ReadRequest", target_dir: Path) -> None:
    """Print a verification read of the patched code to stdout.

    Saves the agent a turn: rather than re-reading the file it just changed —
    and pulling the whole thing back into context — it gets the same focused
    slice it would have asked for, already scoped to the patch.
    """
    from cellsmith.reader import build_graph
    from cellsmith.reader.compiler import compile_read

    try:
        graph = build_graph(target_dir)
        print("\n# ===== POST-PATCH READ =====")
        print(compile_read(graph, request))
    except KeyError as e:
        logging.warning(f"post_patch_read skipped: {e}")
# %% [func:_run_post_patch_read:end]


# %% [func:reannotate_file:start]
def reannotate_file(filepath: Path, target_dir: Path, header: str = None) -> None:
    """Regenerate `filepath`'s markers, preserving its schema header variant.

    Pass `header` to force a variant; otherwise it is detected from the file
    (which must therefore happen before the file is modified).
    """
    if header is None:
        header = _detect_header(filepath, target_dir)
    annotate_file(filepath, header=header)
# %% [func:reannotate_file:end]


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

    YAML files always receive the laconic POINTER_HEADER.
    For Python files, prefer whatever variant the file already carries; for files with no
    header (fresh REPLACE / FILE_CREATE payloads), use the pointer header
    if the project has a skill doc at the target root, else the full header.
    """
    if filepath.suffix in (".yaml", ".yml"):
        return POINTER_HEADER

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
def apply_revisions(
    data: dict,
    target_dir: Path,
    *,
    trace: bool = False,
    json_file: Optional[Path] = None,
) -> bool:
    """Apply a patch payload.

    Prints an ordered, numbered per-operation report (OK / FAILED with a
    corrective instruction) so an agent can re-emit only what failed.
    After applying, every touched .py or .yaml file is stripped and re-annotated so
    markers stay in perfect alignment regardless of what the payload
    contained. Returns True only if every operation succeeded.
    """
    changelog_entries = _validate_changelog(data.get("changelog"))
    _check_ambiguity(data, target_dir)
    post_read = data.get("post_patch_read")
    read_request = ReadRequest.from_dict(post_read) if post_read else None
    telemetry = bool(data.get("telemetry", False)) or trace
    backed_up_files = set()
    results: List[Tuple[bool, str]] = []
    touched_files: dict = {}
    patched_cells: dict = {}

    def _apply_splice_node(rev: dict, target_dir: Path) -> Tuple[bool, str, Optional[Path], List[Path]]:
        from cellsmith.adapters.dag import DAGValidationError, splice_node

        rel_filename = rev.get("filename", ".")
        workflow_dir = (target_dir / rel_filename).resolve()
        after_id = rev.get("after_id")
        new_id = rev.get("new_id")
        node_type = rev.get("node_type", "step")
        code_content = rev.get("code_content")
        statuses = rev.get("statuses", ["Succeeded"])

        if not new_id:
            return False, "SPLICE_NODE requires 'new_id' to be specified.", None, []

        try:
            created_path, modified_paths = splice_node(
                target_dir=workflow_dir,
                after_id=after_id,
                new_id=new_id,
                node_type=node_type,
                raw_content=code_content,
                statuses=statuses,
            )
            return True, f"spliced {new_id} into {workflow_dir.name} -> {created_path.name}", created_path, modified_paths
        except (ValueError, FileNotFoundError, DAGValidationError) as err:
            return False, str(err), None, []

    def _ensure_backup(filepath: Path):
        if filepath not in backed_up_files:
            create_backup(filepath, target_dir)
            backed_up_files.add(filepath)

    def _ok(label: str, message: str) -> None:
        results.append((True, f"{label} OK - {message}"))

    def _fail(label: str, message: str) -> None:
        results.append((False, f"{label} FAILED - {message}"))

    def _touch(filepath: Path) -> None:
        if filepath.suffix in (".py", ".yaml", ".yml") and filepath not in touched_files:
            touched_files[filepath] = _detect_header(filepath, target_dir)

    def _note_cell(filepath: Path, cell_id: str) -> None:
        patched_cells.setdefault(filepath, set()).add(cell_id)

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
        "ARCHIVE": "Archive",
        "FILE_DELETE": "File Delete",
        "SPLICE_NODE": "Splice Node",
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

        if rev_type in ("ARCHIVE", "FILE_DELETE"):
            verb = "archived" if rev_type == "ARCHIVE" else "deleted"
            if not target_file.exists():
                _fail(label, f"cannot {verb[:-1]} {filename}: file does not exist.")
                continue
            ensure_support_dirs(target_dir)
            destination = store_in_archive(target_file, target_dir)
            _ok(label, f"{verb} {filename} (recoverable at {destination})")
            continue

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

        if rev_type == "SPLICE_NODE":
            ok, msg, created_path, modified_paths = _apply_splice_node(rev, target_dir)
            if ok:
                if created_path:
                    _touch(created_path)
                for p in modified_paths:
                    if p.is_file():
                        _touch(p)
                _ok(label, msg)
            else:
                _fail(label, msg)
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

            with open(target_file, "r", encoding="utf-8") as f:
                lines = f.readlines()

            marker = f"# %% [{cell_id}]"
            if not marker.endswith(":start]") and not marker.endswith(":end]"):
                start_marker = f"# %% [{cell_id}:start]"
            else:
                start_marker = marker

            start_idx = -1
            is_paired = False
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped == start_marker:
                    start_idx = i
                    is_paired = ":start]" in start_marker
                    marker = start_marker
                    break
                elif stripped == marker:
                    start_idx = i
                    is_paired = False
                    break

            if start_idx == -1:
                _fail(label, (
                    f"marker `{marker}` not found in {filename}. `cell_id` must exactly "
                    "match a marker in the CURRENT file — re-read the file skeleton if unsure."
                ))
                continue

            if not is_paired and start_idx > 0:
                rewind_idx = start_idx - 1
                while rewind_idx >= 0:
                    prev = lines[rewind_idx].strip()
                    if prev.startswith("# %% ["):
                        break
                    if prev.startswith("import ") or prev.startswith("from "):
                        start_idx = rewind_idx
                    rewind_idx -= 1

            expected_end_marker = marker.replace(":start]", ":end]") if is_paired else None
            end_idx = -1
            for i in range(start_idx + 1, len(lines)):
                stripped = lines[i].strip()
                if is_paired:
                    if stripped == expected_end_marker:
                        end_idx = i + 1
                        break
                else:
                    if stripped.startswith("# %% ["):
                        end_idx = i
                        break
            if end_idx == -1:
                end_idx = len(lines)

            if not code.endswith("\n"):
                code += "\n"
            new_lines = lines[:start_idx] + [code] + lines[end_idx:]
            _ensure_backup(target_file)
            _touch(target_file)
            with open(target_file, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
            _note_cell(target_file, cell_id)
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
                if ":start]" in anchor:
                    end_anchor = anchor.replace(":start]", ":end]")
                    for i in range(a_idx + 1, len(lines)):
                        if lines[i].strip() == end_anchor:
                            a_idx = i
                            break
                insert_idx = a_idx + 1
                placement = f"after {append_after}"
            else:
                for i, line in enumerate(lines):
                    if line.strip() in ("# %% [module:main_guard]", "# %% [module:main_guard:start]"):
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

    for filepath, header in touched_files.items():
        if not filepath.exists():
            continue
        label = f"Post-check [{filepath.name}]"
        
        if filepath.suffix == ".py":
            try:
                ast.parse(filepath.read_text(encoding="utf-8"))
            except SyntaxError as e:
                _fail(label, (
                    f"patched file no longer parses (line {e.lineno}: {e.msg}). Re-emit the "
                    "affected cell completely via CELL_PATCH, or the whole file via REPLACE."
                ))
                continue
        elif filepath.suffix in (".yaml", ".yml"):
            try:
                import yaml
                yaml.safe_load(filepath.read_text(encoding="utf-8"))
            except ImportError:
                pass
            except Exception as e:
                _fail(label, (
                    f"patched YAML file no longer parses: {e}. Re-emit the "
                    "affected cell completely via CELL_PATCH, or the whole file via REPLACE."
                ))
                continue

        reannotate_file(filepath, target_dir, header=header)

    if telemetry:
        _instrument_patched(touched_files, patched_cells, target_dir, results)

    for _, line in results:
        print(line)

    any_success = any(ok for ok, _ in results)
    all_ok = all(ok for ok, _ in results)
    if any_success or not results:
        _write_changelog(changelog_entries, target_dir)
    else:
        logging.warning("No operations applied; changelog not recorded.")

    if any_success and json_file is not None:
        store_patch_file(json_file, target_dir, data.get("patch_name"))

    if all_ok and read_request is not None:
        _run_post_patch_read(read_request, target_dir)
    return all_ok
# %% [func:apply_revisions:end]

# %% [func:rollback_revisions:start]
def rollback_revisions(data: dict, target_dir: Path) -> None:
    """Reverts changes applied by a JSON patch."""
    revisions = data.get("revisions", [])
    for rev in revisions:
        rev_type = rev.get("revision_type")
        if rev_type in ("ARCHIVE", "FILE_DELETE"):
            original = target_dir / rev["filename"]
            if restore_from_archive(original, target_dir):
                logging.info(f"Restored from archive (rollback): {original}")
            continue

        if rev_type == "FILE_MOVE":
            old_file = target_dir / rev["filename"]
            new_file = target_dir / rev.get("new_filename", "")
            if new_file.exists():
                old_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(new_file, old_file)
                logging.info(f"Reverted FILE_MOVE: {new_file} -> {old_file}")
            continue

        if rev_type == "SPLICE_NODE":
            from cellsmith.adapters.dag import unsplice_node
            rel_filename = rev.get("filename", ".")
            workflow_dir = (target_dir / rel_filename).resolve()
            new_id = rev.get("new_id")
            after_id = rev.get("after_id")
            if workflow_dir.exists() and new_id:
                try:
                    unsplice_node(workflow_dir, new_id=new_id, after_id=after_id)
                    logging.info(f"Rolled back SPLICE_NODE: removed {new_id} from {workflow_dir.name}")
                except Exception as e:
                    logging.warning(f"Failed to rollback SPLICE_NODE for {new_id}: {e}")
            continue

    def _restore_from_backup(target_file: Path) -> bool:
        current = backup_path(target_file, target_dir)
        legacy = False
        if not current.exists():
            current = legacy_backup_path(target_file)
            legacy = True
        if not current.exists():
            return False

        target_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(current, target_file)
        current.unlink()
        logging.info(f"Restored file from backup (rollback): {target_file}")

        def _at(index: int) -> Path:
            return (
                legacy_backup_path(target_file, index) if legacy
                else backup_path(target_file, target_dir, index)
            )

        idx = 1
        while _at(idx).exists():
            shutil.move(str(_at(idx)), str(_at(idx - 1)))
            idx += 1
        return True

    artifacts = data.get("artifacts", [])
    for artifact in artifacts:
        target_file = target_dir / artifact["filename"]
        if _restore_from_backup(target_file):
            continue
        if target_file.exists():
            target_file.unlink()
            logging.info(f"Removed created artifact (rollback): {target_file}")

    for rev in revisions:
        rev_type = rev.get("revision_type")
        if rev_type in ("FILE_MOVE", "ARCHIVE", "FILE_DELETE", "SPLICE_NODE"):
            continue

        target_file = target_dir / rev["filename"]

        if _restore_from_backup(target_file):
            continue

        if rev_type in ("FILE_CREATE", "REPLACE") and target_file.exists():
            target_file.unlink()
            logging.info(f"Removed created file (rollback): {target_file}")
# %% [func:rollback_revisions:end]
