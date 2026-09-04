# filepath: examples/hello_world/hello.py
# %% [ai_schema:pointer]
# CellSmith-annotated file. Cells marked with `# %% [<cell_id>]`.
# To modify safely: load `CELLSMITH_PATCH_SCHEMA.md` at the project root
# for the full JSON patch schema (incl. required `changelog` block).
# Run `cellsmith status` first — if it errors, ignore these markers and
# edit the file directly per the user's request.
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
