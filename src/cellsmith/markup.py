# filepath: src/cellsmith/markup.py
# %% [ai_schema:pointer]
# CellSmith-annotated file. Cells marked with `# %% [<cell_id>]`.
# To modify safely: load `CELLSMITH_PATCH_SCHEMA.md` at the project root
# for the full JSON patch schema (incl. required `changelog` block).
# Run `cellsmith status` first — if it errors, ignore these markers and
# edit the file directly per the user's request.
# %% [ai_schema:end]

# %% [module:init:start]
"""Deprecated compatibility shim.

The engine used to live here as a single module. It now lives in the
`cellsmith` package proper — see `annotator`, `patcher`, `files`, `cli` and
`constants`. This module re-exports the old public names so existing imports
(and the pre-0.2 `cellsmith.markup:main` entrypoint) keep working.
"""
# %% [module:init:end]

# %% [imports:start]
from cellsmith import __version__
from cellsmith.annotator import CellAnnotator, annotate_file
from cellsmith.cli import main
from cellsmith.constants import (
    CHANGELOG_FILE,
    FULL_SCHEMA_HEADER,
    POINTER_HEADER,
    SKILL_DOC_FILENAME,
    SKILL_DOC_MARKDOWN,
    VALID_CHANGE_TYPES,
)
from cellsmith.files import (
    _load_gitignore,
    create_backup,
    iter_target_files,
    strip_file,
)
from cellsmith.patcher import (
    _detect_header,
    _validate_changelog,
    _write_changelog,
    apply_revisions,
    rollback_revisions,
    write_skill_doc,
)
# %% [imports:end]

# %% [module:init:2:start]
__all__ = [
    "__version__",
    "CellAnnotator",
    "annotate_file",
    "apply_revisions",
    "rollback_revisions",
    "write_skill_doc",
    "create_backup",
    "iter_target_files",
    "strip_file",
    "main",
    "CHANGELOG_FILE",
    "SKILL_DOC_FILENAME",
    "VALID_CHANGE_TYPES",
    "FULL_SCHEMA_HEADER",
    "POINTER_HEADER",
    "SKILL_DOC_MARKDOWN",
    # Private helpers, re-exported because they shared the monolith's namespace.
    "_load_gitignore",
    "_detect_header",
    "_validate_changelog",
    "_write_changelog",
]
# %% [module:init:2:end]

# %% [module:main_guard:start]
if __name__ == "__main__":
    main()
# %% [module:main_guard:end]
