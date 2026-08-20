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
from typing import List
# %% [imports:end]


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
        return [target] if target.suffix in (".py", ".yaml", ".yml") else []

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
        if path.suffix not in (".py", ".yaml", ".yml"):
            continue
        if spec is not None and spec.match_file(rel.as_posix()):
            continue

        results.append(path)
    return sorted(results)
# %% [func:iter_target_files:end]
