# filepath: src/cellsmith/__main__.py
# %% [ai_schema:pointer]
# CellSmith-annotated file. Cells marked with `# %% [<cell_id>]`.
# To modify safely: load `CELLSMITH_PATCH_SCHEMA.md` at the project root
# for the full JSON patch schema (incl. required `changelog` block).
# Run `cellsmith status` first — if it errors, ignore these markers and
# edit the file directly per the user's request.
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
