# filepath: examples/hello_world/hello.py
# %% [ai_schema:pointer]
# CellSmith workflow DAG node. Cells marked with `# %% [<cell_id>]`.
# To modify or splice: load `CELLSMITH_PATCH_SCHEMA.md` at the project root
# for the workflow DAG patch schema (incl. SPLICE_NODE and changelog rules).
# Run `cellsmith status` first — if it errors, edit files directly.
# %% [ai_schema:end]
# %% [func:greet:start]
def greet(name):
    return f"Hello, {name}!"
# %% [func:greet:end]


# %% [func:main:start]
def main():
    print(greet("world"))
# %% [func:main:end]


# %% [module:main_guard:start]
if __name__ == "__main__":
    main()
# %% [module:main_guard:end]
