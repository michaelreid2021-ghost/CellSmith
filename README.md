# CellSmith
 
Schema-driven code patching for LLM workflows.
 
CellSmith annotates Python source with stable AST-derived cell markers so language models can return compact JSON patches targeting individual functions, methods, classes, or module sections instead of regenerating entire files.
 
It parses the AST, injects non-destructive Jupyter-style markers, validates patches, manages versioned backups, and supports one-command rollback.
 
## Features
 
- **AST-aware annotation** — Automatically places markers around imports, functions, classes, methods, and module sections (`# %% [func:name:start]`, `# %% [class:Foo:start]`, `# %% [method:Cls.x:start]`, protected `# %% [knobs:*]` zones). Nested functions inherit their parent cell.
- **Surgical JSON patching** — Supports `CELL_PATCH`, `REPLACE`, `CELL_CREATE`, `FILE_CREATE`, `FILE_MOVE`, `FILE_DELETE`, and `ARCHIVE` in a single payload.
- **Recoverable deletes** — `FILE_DELETE` removes a file from the working tree and keeps its content in `.cellsmith/archive/`, so a delete made mid-refactor can be undone without discarding every change since the last commit.
- **Token-efficient agent mode** — `annotate-agent` replaces full schema headers with short pointers and writes a single shared `CELLSMITH_PATCH_SCHEMA.md` at the project root.
- **Mandatory changelog gate** — Every patch must contain a validated `changelog` block. Invalid or missing entries are rejected before any disk writes.
- **Dynamic resolution context** — `cellsmith read` renders a call-graph slice at three fidelities: full code on the execution trace, signature-and-docstring skeletons beyond it, one-line summaries further out. Bounded by a character budget applied at cell boundaries. Four discovery flags (`--list-start-cell`, `--tree`, `--get-cell-list`, `--get-file-contents`) answer orientation questions so agents never reach for `cat`, `ls` or `find`.
- **Unambiguous targeting** — A `cell_id` must resolve to exactly one marker. Duplicate markers are rejected with exit code `4` before any write. `cellsmith reannotate` regenerates markers from the AST.
- **Ephemeral focal telemetry** — `patch --trace` wraps the patched cells in a `@focal_trace` decorator that writes one JSON record per call to `.agents/logs/focal_session.jsonl`. `cellsmith finalize` removes it again.
- **Safety defaults** — Automatic versioned backups, post-patch syntax validation, and atomic rollback.
## Installation
 
```bash
git clone https://github.com/michaelreid2021-ghost/CellSmith.git
cd CellSmith
pip install .
```
 
For local development (editable install):
 
```bash
pip install -e .
```
 
## Quick Start
 
### Annotate
 
```bash
# Single file (full schema embedded)
cellsmith annotate path/to/file.py
 
# Project (respects .gitignore by default)
cellsmith annotate .
 
# Agentic mode (short pointers + root schema skill file)
cellsmith annotate-agent .
```
 
### Apply a patch
 
```bash
cellsmith patch patch.json .
```
 
### Rollback
 
```bash
cellsmith rollback patch.json .
```
 
### Probe availability (used by agents)
 
```bash
cellsmith status
# → available cellsmith <version>
```
 
### Re-annotate (regenerate markers)

```bash
# Strip and re-mark from the AST, preserving each file's header variant.
# Fixes drifted or duplicated markers after a hand edit.
cellsmith reannotate src/
```

### Strip annotations
 
```bash
cellsmith strip . -y
```
 
## JSON Patch Contract
 
```json
{
  "revisions": [
    {
      "filename": "auth.py",
      "revision_type": "CELL_PATCH",
      "cell_id": "method:UserService.validate:start",
      "code_content": "# %% [method:UserService.validate:start]\n    def validate(self, ...):\n        ...\n# %% [method:UserService.validate:end]\n"
    }
  ],
  "changelog": [
    {
      "change_type": "bug_fix",
      "summary": "validate() now rejects empty bearer tokens before hitting the DB."
    }
  ]
}
```
 
### Supported revision types
 
| Type | Purpose |
|---|---|
| `CELL_PATCH` | Replace an existing annotated cell |
| `CELL_CREATE` | Insert new code after a specified cell |
| `REPLACE` | Full file replacement (any file type) |
| `FILE_CREATE` | Create a new file |
| `FILE_MOVE` | Rename or move a file |
 
## Changelog Gate
 
`cellsmith patch` rejects any payload that does not contain a valid `changelog` array with at least one entry.
 
Accepted `change_type` values:
 
- `new_feature`
- `correcting_implementation`
- `bug_fix`
- `refactor`
- `schema_migration`
`summary` must be a concise affirmative sentence describing the final state. Valid entries are appended to `CHANGELOG.cellsmith.jsonl` at the target directory root.
 
## Error Handling & Patch Reports
 
`cellsmith patch` doesn't stop at the first bad revision. It works through
every artifact and revision in the payload, applies what it can, and keeps
going past failures — then prints a single ordered report at the end, one
numbered line per operation, in the exact order it was encountered:
 
```
Cell Patch [1] OK - patched func:validate_token in auth.py
Cell Patch [2] FAILED - marker `# %% [method:UserService.validate:start]` not found in auth.py. `cell_id` must exactly match a marker in the CURRENT file — re-read the file skeleton if unsure.
Replace [3] OK - replaced utils.py
```
 
- Each `FAILED` line carries a plain-language, corrective instruction (not a
  traceback) — enough to tell an LLM agent or a person exactly what went
  wrong and how to fix it.
- Because the report lists every operation, not just the failures, whoever
  picks it back up (agent or human) can see precisely what already
  succeeded — so there's no temptation to redo work on unrelated files or
  functions that applied cleanly.
- **Only the FAILED entries should be re-emitted.** The OK ones are already
  written to disk; resending them wastes tokens and can double-apply
  `CELL_CREATE` inserts.
- Post-patch AST validation failures are reported the same way
  (`Post-check [file.py] FAILED - ...`), with the pre-patch `.bak` left
  intact so nothing is lost.
- Exit codes reflect the outcome: `0` everything applied, `2` the changelog
  gate rejected the payload (nothing was written at all), `3` partial —
  some operations succeeded and some failed, `4` ambiguous destination
  (nothing was written at all).
- A `cell_id` must resolve to exactly one marker. If a file carries the same
  marker twice, the patch is refused in full rather than guessing — the
  report names the duplicated lines and shows what each block's `cell_id`
  becomes after `cellsmith reannotate`.
## Agentic Workflows
 
`cellsmith annotate-agent` is intended for multi-file agent sessions:
 
- Each annotated file receives a short (~5-line) pointer header.
- The full schema is written once as `CELLSMITH_PATCH_SCHEMA.md` at the project root.
- Agents are instructed to run `cellsmith status` first. A successful status check enables JSON patching; failure causes the agent to fall back to normal editing and ignore the markers.
## Safety
 
- Versioned backups (`.bak`, `.bak.1`, …) are created before any existing file is modified.
- Post-patch AST syntax validation is performed. Failures leave the backup intact and report the error.
- After a successful patch, Python files are stripped and re-annotated from the AST so markers stay aligned.
- `cellsmith rollback` restores files, removes newly created artifacts, and reverses moves.
## Dynamic Resolution Context (`cellsmith read`)

Agents burn context reading whole files. `cellsmith read` compiles a focused
slice of the call graph instead: full code along the execution trace, AST
skeletons beyond it, one-line summaries in the far background.

```bash
# Flags
cellsmith read --entry app.py:func:process --trace-depth 2 --ast 1 .

# Or a JSON request, which is what agents should emit
cellsmith read read_request.json .
```

```json
{
  "read_request": {
    "entry": "app.py:func:process:start",
    "trace_depth": 2,
    "trace_type": "branching",
    "ast_layers": 1,
    "laconic_background_layers": 1,
    "max_characters": 50000,
    "trace_exclude_paths": ["func:noisy_helper"],
    "trace_keep": ["func:critical"]
  }
}
```

- `trace_type` controls how wide a call site may be to be followed:
  `linear` (straight-line only), `branching` (adds `if`/`try`), `loops`
  (adds `for`/`while`), or `all`.
- Docstrings are stripped from full-fidelity cells and kept on skeletons —
  the summary layer lives in the code, not in a sidecar index.
- The budget is evaluated only at cell boundaries, with a 500-character
  grace buffer, so a function is never cut in half. Cells that don't fit are
  replaced by a `[TRACE_TRUNCATED]` breadcrumb.
- Add `post_patch_read` to a patch payload to get the same focused read back
  automatically after a successful patch.

### Orientation subcommands

A trace needs an entry cell, and finding one is an orientation problem. Four
discovery flags on `read` answer the questions that would otherwise reach
for `cat`, `ls` or `find`. They are mutually exclusive, take a target
directory only (no `--entry`, no JSON request), skip the graph build
entirely, and every answer points back at the cell-aware tools:

```bash
# Where does execution begin? Manifests are ranked — pyproject.toml
# console scripts, setup.cfg/setup.py entry points, Dockerfile*
# ENTRYPOINT/CMD — with a main.py/app.py fallback when nothing resolves.
cellsmith read --list-start-cell .

# The whole project in one call. Honors .gitignore and .ignore at every
# level, includes hidden files, skips .git and .cellsmith/.
cellsmith read --tree .

# Table of contents for one supported file (.py/.yaml/.yml): every cell
# with line spans, plus a suggested entry cell.
cellsmith read --get-cell-list auth.py .

# Raw text — but only for types CellSmith does not parse. Supported types
# get an alert pointing at the cell-aware tools instead (the anti-cat
# guard), and output is capped at 100,000 characters.
cellsmith read --get-file-contents docs/flow.md .
```

The intended cold start: `--list-start-cell` to find where execution
begins, `--get-cell-list` to see what is in that file, then a normal
`read --entry` to pull the trace.

`cellsmith read` exit codes: `0` compiled (or a discovery report printed),
`2` invalid request (unknown field, wrong type, bad `trace_type`) or
conflicting discovery flags, `5` `entry` matched no cell, or matched more
than one. Discovery additionally exits `1` on a missing or unparseable
file.

## Workspace Layout

CellSmith keeps its bookkeeping under one hidden directory at the patch
target root:

```text
.cellsmith/archive/    files retired by an ARCHIVE revision
.cellsmith/backups/    pre-patch copies, used by rollback
.cellsmith/patches/    payloads that have been applied, plus index.jsonl
CHANGELOG.cellsmith.jsonl   stays at the project root
```

`.cellsmith/` is added to `.gitignore` on first use, and the `.gitignore` is
created if the project has none. Paths under `archive/` and `backups/` mirror
the file's path relative to the project root, so `a/m.py` and `b/m.py` do not
collide.

Backups used to be written as `.bak` siblings of each source file. Rollback
still reads those, so backups taken by an older version keep working.

### `FILE_DELETE` and `ARCHIVE`

```json
{"filename": "pkg/legacy.py", "revision_type": "FILE_DELETE"}
```

The file leaves the working tree, so git reports it as deleted at the next
commit, the same as a plain `rm`. Its content moves to
`.cellsmith/archive/pkg/legacy.py`, and `cellsmith rollback` on that payload
brings it back.

The point is recovery between commits. Git can only return you to the last
commit, so deleting a file at step 10 of a 15-step refactor otherwise means
throwing away all 15 steps to recover it. Rolling back one payload restores
the file and leaves the rest of the work in place.

Deleting the same path more than once in a session keeps every version —
`legacy.py`, `legacy.py.1`, and so on — so rolling the payloads back in
reverse order restores the right version each time.

`ARCHIVE` is the same mechanism under a different name, for retiring a file
you may want back rather than deleting one.

### `patch_name`

Optional, at the top level of a payload:

```json
{"patch_name": "0007-fix-rounding.json", "revisions": [], "changelog": []}
```

After a payload is applied it moves into `.cellsmith/patches/`, under
`patch_name` if given. Payloads without the key keep their original filename,
so existing patch files are unaffected. A rejected payload stays where it is.

`.cellsmith/patches/index.jsonl` records the name each payload arrived under
and the name it was filed as, so `cellsmith rollback old-name.json` still
resolves after a rename.

## Focal Telemetry (`--trace`, `finalize`)

Application logs are chronological and mix every subsystem together. After
patching one function, an agent needs that function's inputs, locals, and
exceptions, not a filtered stream.

`cellsmith patch --trace` wraps each patched cell in a `@focal_trace`
decorator and installs a zero-dependency runtime at
`.agents/cellsmith_telemetry.py`. `.agents/` is added to `.gitignore`.

```bash
cellsmith patch patch.json . --trace   # patch, then instrument the patched cells
python -m pytest                       # or run the app
cat .agents/logs/focal_session.jsonl   # one JSON record per call
cellsmith finalize .                   # strip all instrumentation
```

Each record carries the `cell_id`, `args`, `kwargs`, `locals`, `return` or
`exception`, `raised_at_line`, and `duration_ms`. Values whose name contains
`password`, `secret`, `token`, `api_key`, or `authorization` are written as
`<redacted>`. Long values are truncated.

Instrumentation is opt-in. It is applied by `--trace`, or by setting
`"telemetry": true` on the patch payload. Without either, `patch` never
modifies a function's decorators.

Other commands:

```bash
# Install the runtime without patching; optionally instrument a file
cellsmith telemetry . --instrument app.py
cellsmith telemetry . --instrument app.py --cells func:process method:Cls.run
```

Notes:

- The decorator is written above the cell's `:start` marker, so it sits
  outside the cell. A later `CELL_PATCH` replaces `:start` through `:end` and
  leaves the instrumentation in place; the agent keeps emitting pure logic.
- Local capture installs one shared trace function. It is skipped when a
  debugger, profiler, or coverage tool already holds the trace slot, in which
  case `locals` is empty and everything else is still recorded.
- Nested functions are never wrapped. They belong to their parent's cell.
- `cellsmith finalize` must be run before committing. It removes every
  `@focal_trace` decorator and the import preamble.

## Project Layout
 
```text
src/cellsmith/
├── __init__.py       # version + public API
├── cli.py            # argparse setup and command routing
├── annotator.py      # AST/YAML traversal: CellAnnotator, annotate_file()
├── patcher.py        # changelog gate, apply_revisions(), rollback_revisions()
├── files.py          # backups, strip_file(), target discovery, .gitignore
├── constants.py      # shared constants + template loader
├── telemetry.py      # @focal_trace injection, stripping, finalize
├── workspace.py      # .cellsmith/ support dirs, backups, patch filing
├── survey.py         # read discovery: start cells, cell list, tree, raw contents
├── reader/           # CellRead subsystem
│   ├── graph.py      # CellGraph: cells + statically resolved call edges
│   ├── compiler.py   # mixed-fidelity renderer (full / skeleton / laconic)
│   ├── budget.py     # character budget with grace buffer
│   └── schema.py     # ReadRequest validation
└── templates/        # static assets (schema headers, skill doc)
```
 
`cellsmith.markup` remains as a deprecated shim re-exporting the public names.
 
## License
 
MIT
