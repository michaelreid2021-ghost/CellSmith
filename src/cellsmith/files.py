# filepath: src/cellsmith/files.py
# %% [ai_schema:pointer]
# CellSmith-annotated file. Cells marked with `# %% [<cell_id>]`.
# To modify safely: load `CELLSMITH_PATCH_SCHEMA.md` at the project root
# for the full JSON patch schema (incl. required `changelog` block).
# Run `cellsmith status` first — if it errors, ignore these markers and
# edit the file directly per the user's request.
# %% [ai_schema:end]

# %% [module:init:start]
"""Filesystem utilities: backups, marker stripping, and target discovery."""
# %% [module:init:end]

# %% [imports:start]
import logging
import shutil
from pathlib import Path
from typing import List, Optional, Tuple

from cellsmith.constants import SUPPORTED_SUFFIXES
from cellsmith.workspace import backup_path, support_root
# %% [imports:end]


# %% [func:create_backup:start]
def create_backup(filepath: Path, root: Optional[Path] = None) -> Optional[Path]:
    """Copy `filepath` into the project's backup store, rotating older copies.

    Backups live under `.cellsmith/backups/`, mirroring the file's path
    relative to `root`, rather than as `.bak` siblings of the source. Returns
    the path written, or None when there was nothing to back up.
    """
    if not filepath.exists():
        return None

    root = root if root is not None else filepath.parent

    backup_idx = 1
    while backup_path(filepath, root, backup_idx).exists():
        backup_idx += 1

    for i in range(backup_idx - 1, 0, -1):
        shutil.move(str(backup_path(filepath, root, i)), str(backup_path(filepath, root, i + 1)))

    current = backup_path(filepath, root)
    current.parent.mkdir(parents=True, exist_ok=True)
    if current.exists():
        shutil.move(str(current), str(backup_path(filepath, root, 1)))

    shutil.copy2(filepath, current)
    logging.info(f"Created versioned backup: {current}")
    return current
# %% [func:create_backup:end]


# %% [func:strip_lines:start]
def strip_lines(
    lines: List[str],
    *,
    strip_prompt: bool = True,
    strip_markers: bool = True,
) -> List[str]:
    """Return `lines` without the AI schema header and/or `# %% [...]` markers.

    Pure counterpart to `strip_file`, for callers that need the stripped form
    of a file without touching disk — e.g. previewing what a clean
    re-annotation would produce.

    `# %% [knobs:...]` markers are always preserved — they delimit
    user-authored protected blocks, not CellSmith annotations.
    """
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

    return out
# %% [func:strip_lines:end]


# %% [func:strip_file:start]
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

    out = strip_lines(lines, strip_prompt=strip_prompt, strip_markers=strip_markers)

    removed = len(lines) - len(out)
    if removed:
        with open(filepath, "w", encoding="utf-8") as f:
            f.writelines(out)
    return removed
# %% [func:strip_file:end]


# %% [func:build_ignore_spec:start]
def build_ignore_spec(lines: List[str]):
    """Compile gitwildmatch ignore lines into a pathspec.PathSpec, or None."""
    if not lines:
        return None
    try:
        import pathspec
    except ImportError:
        logging.warning("pathspec not installed; skipping ignore filtering")
        return None
    return pathspec.PathSpec.from_lines("gitwildmatch", list(lines))
# %% [func:build_ignore_spec:end]


# %% [func:load_ignore_spec:start]
def load_ignore_spec(directory: Path, names: Tuple[str, ...] = (".gitignore", ".ignore")):
    """Return a pathspec.PathSpec built from the named ignore files in `directory`, or None."""
    lines: List[str] = []
    for name in names:
        candidate = directory / name
        if candidate.is_file():
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    lines.extend(f.readlines())
            except (OSError, UnicodeDecodeError):
                continue
    return build_ignore_spec(lines)
# %% [func:load_ignore_spec:end]


# %% [func:_load_gitignore:start]
def _load_gitignore(root: Path):
    """Return a pathspec.PathSpec built from <root>/.gitignore, or None."""
    return load_ignore_spec(root, names=(".gitignore",))
# %% [func:_load_gitignore:end]


# %% [func:iter_target_files:start]
def iter_target_files(
    target: Path,
    *,
    use_gitignore: bool = True,
    include_hidden: bool = False,
) -> List[Path]:
    """Walk `target`, yielding Python and YAML files to annotate.

    Skips dotted dirs/files (.git, .venv, ...) and dunder dirs (__pycache__, ...)
    by default, and honors the nearest .gitignore at `target` if present.
    """
    if target.is_file():
        return [target] if target.suffix in SUPPORTED_SUFFIXES else []

    spec = _load_gitignore(target) if use_gitignore else None
    support = support_root(target).resolve()
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
        # CellSmith's own support tree lives inside the project. Its
        # backups and archived files are copies of real sources, so they must
        # never be walked as if they were part of the project.
        if support in path.resolve().parents:
            continue
        if path.suffix not in SUPPORTED_SUFFIXES:
            continue
        if spec is not None and spec.match_file(rel.as_posix()):
            continue

        results.append(path)
    return sorted(results)
# %% [func:iter_target_files:end]
