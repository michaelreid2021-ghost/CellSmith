# filepath: src/cellsmith/__main__.py
# %% [ai_schema:pointer]
# CellSmith workflow DAG node. Cells marked with `# %% [<cell_id>]`.
# To modify or splice: load `CELLSMITH_PATCH_SCHEMA.md` at the project root
# for the workflow DAG patch schema (incl. SPLICE_NODE and changelog rules).
# Run `cellsmith status` first — if it errors, edit files directly.
# %% [ai_schema:end]
# %% [module:init:start]
"""Allow `python -m cellsmith` as an alias for the `cellsmith` console script."""
# %% [module:init:end]

# %% [imports:start]
from cellsmith.cli import main
# %% [imports:end]

# %% [module:main_guard:start]
if __name__ == "__main__":
    main()
# %% [module:main_guard:end]
