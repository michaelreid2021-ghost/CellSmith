# filepath: src/cellsmith/__init__.py
# %% [ai_schema:pointer]
# CellSmith workflow DAG node. Cells marked with `# %% [<cell_id>]`.
# To modify or splice: load `CELLSMITH_PATCH_SCHEMA.md` at the project root
# for the workflow DAG patch schema (incl. SPLICE_NODE and changelog rules).
# Run `cellsmith status` first — if it errors, edit files directly.
# %% [ai_schema:end]
# %% [module:init:start]
"""CellSmith — AST-based cell-aware code patcher for LLM-driven Python edits."""

__version__ = "0.1.0"
# %% [module:init:end]

# %% [imports:start]
from cellsmith.annotator import CellAnnotator, annotate_file, plan_insertions
from cellsmith.constants import (
    CHANGELOG_FILE,
    FULL_SCHEMA_HEADER,
    POINTER_HEADER,
    SKILL_DOC_FILENAME,
    SKILL_DOC_MARKDOWN,
    VALID_CHANGE_TYPES,
)
from cellsmith.files import create_backup, iter_target_files, strip_file, strip_lines
from cellsmith.patcher import (
    AmbiguousMarkerError,
    apply_revisions,
    reannotate_file,
    rollback_revisions,
    write_skill_doc,
)
# %% [imports:end]

# %% [module:init:2:start]
__all__ = [
    "__version__",
    "CellAnnotator",
    "annotate_file",
    "plan_insertions",
    "reannotate_file",
    "AmbiguousMarkerError",
    "apply_revisions",
    "rollback_revisions",
    "write_skill_doc",
    "create_backup",
    "iter_target_files",
    "strip_file",
    "strip_lines",
    "CHANGELOG_FILE",
    "SKILL_DOC_FILENAME",
    "VALID_CHANGE_TYPES",
    "FULL_SCHEMA_HEADER",
    "POINTER_HEADER",
    "SKILL_DOC_MARKDOWN",
]
# %% [module:init:2:end]
