# CellSmith

A lightweight, schema-driven code patch system that LLMs will love.

CellSmith annotates a Python file with Jupyter-flavored cell markers
(`# %% [func:name]`, `# %% [class:Foo]`, `# %% [method:Cls.x]`, plus protected
`# %% [knobs:*:start]` zones) so an LLM can return surgical JSON patches
against named cells instead of rewriting whole files.

## Why this exists

If you've ever pasted a 600-line Python file into a chat UI and asked an LLM
to "just change the `validate()` method," you've watched it dutifully echo
back all 600 lines — burning tokens, mangling unrelated bits, and forcing you
to diff the result by eye. The model wasn't being lazy; you didn't give it a
way to be precise.

CellSmith gives the model that vocabulary. The annotated file shows it a
**skeleton** — every function, method, class, and `[imports]` block tagged
with a stable `cell_id`. The model returns a tiny JSON like:

```json
{
  "revisions": [
    {"filename": "auth.py", "revision_type": "CELL_PATCH",
     "cell_id": "method:UserService.validate",
     "code_content": "# %% [method:UserService.validate]\n    def validate(self, ...):\n        ..."}
  ],
  "changelog": [
    {"change_type": "bug_fix",
     "summary": "validate() now rejects empty bearer tokens before hitting the DB."}
  ]
}
```

…and CellSmith splices it in. The rest of the file is never re-emitted, never
re-tokenized, never at risk.

### Real workloads it's already chewed through

These are real single-shot LLM outputs that landed cleanly via `cellsmith patch`:

| Payload | Size | Revisions | Tools | Files | Notes |
|---|---|---|---|---|---|
| `patch_tuesday.json` | **98 KB** | 9 | 8× CELL_PATCH + 1× REPLACE | 2 | **Gemini Pro single-shot via the chat UI.** No agent, no SDK loop — just paste-and-go. |
| `big_patch.json` | 40 KB | **74** | 73× CELL_PATCH + 1× REPLACE | 3 | One model, one prompt, 74 surgical edits across three files. |
| `patch_1.json` | 16 KB | 14 | 14× CELL_PATCH | 2 | Typical iterative session. |

You don't need an agent framework to get this leverage. You need a vocabulary
the model can speak fluently, and a tool boring enough not to fight you.

### Use cases

- **Big-codebase chat-UI editing.** Pile up your project's annotated files in
  one prompt; let the model return one JSON; apply it locally. No API plumbing.
- **Local / small-model workflows.** A 4B model on MLX or llama.cpp can land
  surgical CELL_PATCH ops if it doesn't have to re-emit context. Density beats
  parameter count when the tool is shaped right.
- **Multi-file refactors in one shot.** Rename a method across three files,
  add a new helper, tweak a constant — one JSON, one apply, one rollback if
  it breaks.
- **Beyond Python.** Annotation is Python-only today, but `REPLACE` works on
  any file (JSON, TOML, Markdown, configs), so an LLM can drop an entirely new
  `pyproject.toml` or rewrite a `schema.json` in the same payload.

## Install

```bash
pip install -e .
```

## Use

```bash
# 1a. Annotate a single file (full schema embedded in the file header)
cellsmith annotate path/to/file.py

# 1b. Or annotate every .py in a project — respects .gitignore by default,
#     skips dotted dirs (.git/.venv) and dunder dirs (__pycache__).
cellsmith annotate .                  # full project
cellsmith annotate . --dry-run        # preview which files would be touched
cellsmith annotate . --no-gitignore   # ignore .gitignore filtering
cellsmith annotate . --include-hidden # include dotted dirs/files

# 1c. Agentic mode — same cell markers, but each file gets only a short
#     pointer header (~5 lines) instead of the ~30-line schema block. The
#     full schema is dropped once at the project root as a markdown skill
#     file (CELLSMITH_PATCH_SCHEMA.md) so agents seeing many files don't
#     re-tokenize the schema in every one.
cellsmith annotate-agent .

# 2. Hand annotated file(s) to your LLM, get back a JSON patch, save as patch.json
cellsmith patch patch.json .

# 3. Roll back if the patch was bad
cellsmith rollback patch.json .

# 4. Probe whether cellsmith is available (agents use this before patching)
cellsmith status        # prints: available cellsmith 0.1.0

# 5. When you're done, strip cell markers + schema header to get plain code back
cellsmith strip path/to/file.py                # asks for confirmation
cellsmith strip . -y                           # whole project, skip prompt
cellsmith strip path/to/file.py --prompt-only  # keep markers, drop schema only
cellsmith strip path/to/file.py --markers-only # keep schema, drop markers only
```

👉 **[examples/hello_world/WALKTHROUGH.md](examples/hello_world/WALKTHROUGH.md)** —
end-to-end tour: annotate a trivial file, ask any chat-UI LLM to make it
"the most complex Hello World imaginable" (no schema explanation needed —
the file teaches the model), apply the patch.

> Annotation is Python-only (it walks the AST). Patching can target **any**
> file via `REPLACE` (JSON, TOML, Markdown, anything), and `CELL_PATCH` /
> `CELL_CREATE` work on any file that has cell markers — but for now only
> Python files get marker generation out of the box.

## Required: the `changelog` block

Every patch payload **must** include a `changelog` array with at least one
entry. `cellsmith patch` is a **blocking gate** — payloads missing or
malformed in this block are rejected before any disk writes happen.

```json
{
  "revisions": [ ... ],
  "changelog": [
    {
      "change_type": "bug_fix",
      "summary": "Rate limiter now uses a monotonic clock so suspends don't reset the window.",
      "details": [
        "Replaced time.time() with time.monotonic() in RateLimiter.tick()",
        "Added regression test for clock-skew scenario"
      ]
    }
  ]
}
```

| Field | Required | Notes |
|---|---|---|
| `change_type` | yes | One of: `new_feature`, `correcting_implementation`, `bug_fix`, `refactor`, `schema_migration` |
| `summary` | yes | One concise affirmative sentence — describe the **final state**, not past mistakes |
| `details` | no | Array of strings, granular technical bullets |
| `timestamp` | no | ISO-8601 UTC; filled in by `cellsmith patch` if omitted |
| `author` | no | Free-form model/agent identifier |

Accepted entries are appended one-per-line to `CHANGELOG.cellsmith.jsonl` at
the patch target root — a structured, append-only project history you can
read with `jq`, render to Markdown, or feed back to the model as context for
later patches.

## Agentic mode: `annotate-agent` + `status`

In an agent workflow, the LLM often sees many annotated files in one
session. Repeating the ~30-line schema header in every file is wasted
tokens. `cellsmith annotate-agent` solves this:

- Each annotated file gets a **5-line pointer header** instead of the full
  schema, telling the agent to load `CELLSMITH_PATCH_SCHEMA.md` at the
  project root.
- That skill file is written once at the root, in plain markdown, so any
  agent (Claude Code, Cursor, Continue, chat-UI paste, you name it) can
  load it.
- The skill file instructs the agent to run `cellsmith status` **first**:
  - exit 0 (`available cellsmith <version>`) → generate a JSON patch per
    the schema
  - any error (command not found, non-zero exit) → **ignore the cell
    markers and edit the file directly** per the user's original request

That last point is the safety net: if `cellsmith` isn't installed on the
machine where the agent runs, the markers are inert comments and the agent
falls back to normal editing. No installation gymnastics required.

## For chat-agent integrators

If you're wiring CellSmith up to a structured-output chat agent (Gemini
`responseSchema`, OpenAI structured outputs, etc.), the canonical OpenAPI
spec for the response shape lives in
[examples/response_schema.json](examples/response_schema.json). It's the
single source of truth for `content` / `revisions` / `artifacts` /
`changelog` / `task_update`.

## License

MIT
