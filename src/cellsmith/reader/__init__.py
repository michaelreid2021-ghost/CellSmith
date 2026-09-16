# filepath: src/cellsmith/reader/__init__.py
# %% [ai_schema:pointer]
# CellSmith workflow DAG node. Cells marked with `# %% [<cell_id>]`.
# To modify or splice: load `CELLSMITH_PATCH_SCHEMA.md` at the project root
# for the workflow DAG patch schema (incl. SPLICE_NODE and changelog rules).
# Run `cellsmith status` first — if it errors, edit files directly.
# %% [ai_schema:end]
# %% [module:init:start]
"""CellRead — dynamic resolution context compiler.

`graph` indexes a project's cells and the call edges between them; the
remaining modules render a focused slice of that graph within a budget.
"""
# %% [module:init:end]

# %% [imports:start]
from cellsmith.reader.graph import CellGraph, CellNode, build_graph
# %% [imports:end]

# %% [module:init:2:start]
__all__ = ["CellGraph", "CellNode", "build_graph"]
# %% [module:init:2:end]
