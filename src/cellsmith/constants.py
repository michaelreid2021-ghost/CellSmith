# filepath: src/cellsmith/constants.py
# %% [ai_schema:pointer]
# CellSmith-annotated file. Cells marked with `# %% [<cell_id>]`.
# To modify safely: load `CELLSMITH_PATCH_SCHEMA.md` at the project root
# for the full JSON patch schema (incl. required `changelog` block).
# Run `cellsmith status` first — if it errors, ignore these markers and
# edit the file directly per the user's request.
# %% [ai_schema:end]

# %% [module:init:start]
"""Shared constants and the loader for the static text assets.

The AI schema headers and the markdown skill doc live in `templates/` rather
than inline here — they are ~250 lines of prose and would drown the engine
logic. They are read once at import via `importlib.resources`, so they work
both from a checkout and from an installed wheel.
"""
# %% [module:init:end]

# %% [imports:start]
from importlib.resources import files
# %% [imports:end]

# %% [module:init:2:start]
CHANGELOG_FILE = "CHANGELOG.cellsmith.jsonl"
SKILL_DOC_FILENAME = "CELLSMITH_PATCH_SCHEMA.md"
VALID_CHANGE_TYPES = frozenset({
    "new_feature",
    "correcting_implementation",
    "bug_fix",
    "refactor",
    "schema_migration",
})

# File types CellSmith parses into cells — the single source of truth for
# what the annotator, the walkers and the survey tools consider supported.
SUPPORTED_SUFFIXES = (".py", ".yaml", ".yml")
# %% [module:init:2:end]


# %% [func:load_template:start]
def load_template(name: str) -> str:
    """Return the text of `templates/<name>`, with LF line endings."""
    raw = files(__package__).joinpath("templates", name).read_bytes()
    return raw.decode("utf-8").replace("\r\n", "\n")
# %% [func:load_template:end]


# Full in-file schema header — embedded by `cellsmith annotate` so the file
# self-documents the JSON patch contract. Used when the file may be pasted
# into a chat UI without any agent tooling around it.
# %% [module:init:3:start]
FULL_SCHEMA_HEADER = load_template("full_schema_header.txt")

# Laconic pointer header — embedded by `cellsmith annotate-agent` instead of
# FULL_SCHEMA_HEADER, to avoid duplicating ~30 lines of schema instructions
# across every annotated file in an agentic workflow. The skill doc at the
# project root carries the full schema.
POINTER_HEADER = load_template("pointer_header.txt").replace(
    "__SKILL_DOC_FILENAME__", SKILL_DOC_FILENAME
)

# Full schema doc dropped at project root by `cellsmith annotate-agent`.
# Markdown so any agent (Claude Code, Cursor, Continue, chat-UI paste) can
# load it; idempotent — overwritten on every run from this single template.
SKILL_DOC_MARKDOWN = load_template("skill_doc.md")
# %% [module:init:3:end]
