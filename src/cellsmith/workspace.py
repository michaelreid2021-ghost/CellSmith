# filepath: src/cellsmith/workspace.py
# %% [ai_schema:pointer]
# CellSmith-annotated file. Cells marked with `# %% [<cell_id>]`.
# To modify safely: load `CELLSMITH_PATCH_SCHEMA.md` at the project root
# for the full JSON patch schema (incl. required `changelog` block).
# Run `cellsmith status` first — if it errors, ignore these markers and
# edit the file directly per the user's request.
# %% [ai_schema:end]

# %% [module:init:start]
"""Support directories for a patched project.

Everything CellSmith keeps for its own bookkeeping lives under a single
hidden root, `.cellsmith/`:

    .cellsmith/archive/    files retired by an ARCHIVE revision
    .cellsmith/backups/    rotated pre-patch copies
    .cellsmith/patches/    payloads that have been processed

One hidden directory means one `.gitignore` entry and one thing to delete,
and it keeps the working tree free of the `.bak` files that used to sit
beside every patched source. Paths under `archive/` and `backups/` mirror the
file's path relative to the project root, so `a/m.py` and `b/m.py` never
collide.

`CHANGELOG.cellsmith.jsonl` deliberately stays at the project root. It is a
record of what changed, meant to be read and kept, not bookkeeping.
"""
# %% [module:init:end]

# %% [imports:start]
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
# %% [imports:end]

# %% [module:init:2:start]
SUPPORT_DIR = ".cellsmith"
ARCHIVE_DIRNAME = "archive"
BACKUPS_DIRNAME = "backups"
PATCHES_DIRNAME = "patches"
PATCH_INDEX_FILENAME = "index.jsonl"

# Written into .gitignore when the support tree is first created.
IGNORE_ENTRIES = (f"{SUPPORT_DIR}/",)
# %% [module:init:2:end]


# %% [func:support_root:start]
def support_root(root: Path) -> Path:
    return root / SUPPORT_DIR
# %% [func:support_root:end]


# %% [func:archive_dir:start]
def archive_dir(root: Path) -> Path:
    return support_root(root) / ARCHIVE_DIRNAME
# %% [func:archive_dir:end]


# %% [func:backups_dir:start]
def backups_dir(root: Path) -> Path:
    return support_root(root) / BACKUPS_DIRNAME
# %% [func:backups_dir:end]


# %% [func:patches_dir:start]
def patches_dir(root: Path) -> Path:
    return support_root(root) / PATCHES_DIRNAME
# %% [func:patches_dir:end]


# %% [func:_relative_to_root:start]
def _relative_to_root(filepath: Path, root: Path) -> Path:
    """`filepath` relative to `root`, falling back to its bare name.

    A path outside the project root has no meaningful mirrored location, so
    it is stored flat rather than escaping the support directory.
    """
    try:
        return filepath.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return Path(filepath.name)
# %% [func:_relative_to_root:end]


# %% [func:backup_path:start]
def backup_path(filepath: Path, root: Path, index: int = 0) -> Path:
    """Where the `index`-th backup of `filepath` lives.

    Index 0 is the most recent (`m.py.bak`); higher indices are older
    (`m.py.bak.1`, `m.py.bak.2`, ...).
    """
    relative = _relative_to_root(filepath, root)
    suffix = ".bak" if index == 0 else f".bak.{index}"
    return backups_dir(root) / relative.parent / (relative.name + suffix)
# %% [func:backup_path:end]


# %% [func:legacy_backup_path:start]
def legacy_backup_path(filepath: Path, index: int = 0) -> Path:
    """The pre-`.cellsmith` location: a sibling of the source file.

    Kept so a rollback still works against backups taken by an older version.
    """
    suffix = ".bak" if index == 0 else f".bak.{index}"
    return filepath.with_suffix(filepath.suffix + suffix)
# %% [func:legacy_backup_path:end]


# %% [func:archive_path:start]
def archive_path(filepath: Path, root: Path) -> Path:
    """Where an ARCHIVE revision puts `filepath`."""
    return archive_dir(root) / _relative_to_root(filepath, root)
# %% [func:archive_path:end]


# %% [func:ensure_ignored:start]
def ensure_ignored(root: Path) -> None:
    """Make sure `.gitignore` exists and excludes the support directory."""
    gitignore = root / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    missing: List[str] = [e for e in IGNORE_ENTRIES if e not in existing.split()]
    if not missing:
        return

    prefix = "" if not existing or existing.endswith("\n") else "\n"
    with open(gitignore, "a", encoding="utf-8") as handle:
        handle.write(f"{prefix}\n# CellSmith support directories\n")
        for entry in missing:
            handle.write(f"{entry}\n")
    logging.info(f"Added {', '.join(missing)} to {gitignore}")
# %% [func:ensure_ignored:end]


# %% [func:ensure_support_dirs:start]
def ensure_support_dirs(root: Path, *, ignore: bool = True) -> Path:
    """Create `.cellsmith/` and register it in `.gitignore`."""
    target = support_root(root)
    target.mkdir(parents=True, exist_ok=True)
    if ignore:
        ensure_ignored(root)
    return target
# %% [func:ensure_support_dirs:end]


# %% [func:store_patch_file:start]
def store_patch_file(
    json_file: Path,
    root: Path,
    patch_name: Optional[str] = None,
) -> Optional[Path]:
    """Move a processed payload into `.cellsmith/patches/`.

    Named by `patch_name` when the payload carried one. Returns the new path,
    or None when the file could not be moved.
    """
    if not json_file.exists():
        return None

    destination_dir = patches_dir(root)
    destination_dir.mkdir(parents=True, exist_ok=True)

    name = sanitize_patch_name(patch_name) if patch_name else json_file.name
    destination = destination_dir / name
    if destination.resolve() == json_file.resolve():
        return destination

    # Never clobber an earlier payload of the same name.
    if destination.exists():
        stem, suffix = destination.stem, destination.suffix
        index = 1
        while destination.exists():
            destination = destination_dir / f"{stem}.{index}{suffix}"
            index += 1

    original_name = json_file.name
    try:
        shutil.move(str(json_file), str(destination))
    except OSError as e:
        logging.warning(f"Could not move {json_file} to {destination_dir}: {e}")
        return None
    _record_patch(root, original_name, destination)
    logging.info(f"Filed patch payload at {destination}")
    return destination
# %% [func:store_patch_file:end]


# %% [func:patch_index_path:start]
def patch_index_path(root: Path) -> Path:
    return patches_dir(root) / PATCH_INDEX_FILENAME
# %% [func:patch_index_path:end]


# %% [func:_record_patch:start]
def _record_patch(root: Path, original_name: str, destination: Path) -> None:
    """Note where a payload was filed, keyed by the name it arrived under.

    `patch_name` renames the payload, so without this a rollback naming the
    original file would have nothing to match against.
    """
    entry = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "original": original_name,
        "filed": destination.name,
    }
    try:
        with open(patch_index_path(root), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
    except OSError as e:
        logging.warning(f"Could not update the patch index: {e}")
# %% [func:_record_patch:end]


# %% [func:_filed_as:start]
def _filed_as(root: Path, original_name: str) -> Optional[Path]:
    """The most recent filed location for a payload named `original_name`."""
    index = patch_index_path(root)
    if not index.exists():
        return None
    match = None
    try:
        for line in index.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("original") == original_name:
                match = entry.get("filed")
    except OSError:
        return None
    if not match:
        return None
    candidate = patches_dir(root) / match
    return candidate if candidate.exists() else None
# %% [func:_filed_as:end]


# %% [func:filed_patches:start]
def filed_patches(root: Path) -> List[str]:
    """Names of the payloads currently held in `.cellsmith/patches/`."""
    directory = patches_dir(root)
    if not directory.is_dir():
        return []
    return sorted(
        f.name for f in directory.iterdir()
        if f.is_file() and f.name != PATCH_INDEX_FILENAME
    )
# %% [func:filed_patches:end]


# %% [func:sanitize_patch_name:start]
def sanitize_patch_name(name: str) -> str:
    """Reduce `patch_name` to a bare filename ending in `.json`.

    The value comes from a model-authored payload, so any directory part is
    dropped rather than trusted.
    """
    cleaned = Path(str(name).strip().replace("\\", "/")).name
    cleaned = "".join(c for c in cleaned if c.isalnum() or c in "._- ").strip()
    cleaned = cleaned.replace(" ", "_") or "patch"
    if not cleaned.lower().endswith(".json"):
        cleaned += ".json"
    return cleaned
# %% [func:sanitize_patch_name:end]


# %% [func:find_patch_file:start]
def find_patch_file(json_file: Path, root: Path) -> Path:
    """Resolve a payload path, looking in `.cellsmith/patches/` as a fallback.

    `cellsmith patch` files the payload away after applying it, so a later
    `cellsmith rollback` naming the original path would otherwise miss.
    """
    if json_file.exists():
        return json_file
    filed = patches_dir(root) / json_file.name
    if filed.exists():
        return filed
    recorded = _filed_as(root, json_file.name)
    if recorded is not None:
        logging.info(f"Using filed payload {recorded}")
        return recorded
    return json_file
# %% [func:find_patch_file:end]
