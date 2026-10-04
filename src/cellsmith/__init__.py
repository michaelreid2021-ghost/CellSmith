# filepath: src/cellsmith/__init__.py
"""CellSmith — AST-based cell-aware code patcher for LLM-driven Python edits.

CellSmith annotates Python source with stable AST-derived cell markers so language
models can return compact JSON patches targeting individual functions, methods, classes,
or module sections instead of regenerating entire files.

It parses the AST, injects non-destructive Jupyter-style markers, validates patches,
manages versioned backups, and supports one-command rollback.
"""

__version__ = "0.1.0"

# %% [module:public_api:start]
from cellsmith.annotator import CellAnnotator, annotate_file, plan_insertions
from cellsmith.adapters.base import (
    BaseWorkflowAdapter,
    DagNode,
    DagGraph,
    Metadata,
)
from cellsmith.adapters.logic_app import (
    LogicAppAdapter,
    unpack_playbook,
    pack_playbook,
)
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
from cellsmith.reader import build_graph
from cellsmith.survey import (
    cell_list_report,
    file_contents_report,
    start_cell_report,
    tree_report,
)
from cellsmith.telemetry import (
    AGENTS_DIR,
    ensure_runtime,
    finalize_tree,
    instrument_file,
    log_path,
)
from cellsmith.workspace import filed_patches, find_patch_file

# %% [module:adapter_discovery:start]
from cellsmith.registry import load_all_adapters
# %% [module:adapter_discovery:end]

__all__ = [
    # Core
    "__version__",
    "CellAnnotator",
    "annotate_file",
    "plan_insertions",
    "apply_revisions",
    "reannotate_file",
    "rollback_revisions",
    "write_skill_doc",
    "create_backup",
    "iter_target_files",
    "strip_file",
    "strip_lines",
    # Protocol
    "BaseWorkflowAdapter",
    "DagNode",
    "DagGraph",
    "Metadata",
    # Adapters
    "LogicAppAdapter",
    "unpack_playbook",
    "pack_playbook",
    # Discovery
    "load_all_adapters",
    # Constants & utilities
    "CHANGELOG_FILE",
    "FULL_SCHEMA_HEADER",
    "POINTER_HEADER",
    "SKILL_DOC_FILENAME",
    "SKILL_DOC_MARKDOWN",
    "VALID_CHANGE_TYPES",
]
