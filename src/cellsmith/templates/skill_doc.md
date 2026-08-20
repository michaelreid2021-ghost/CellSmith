# CellSmith patch schema

This project's `.py` and `.yaml` files have been annotated by **CellSmith** with cell
markers. Functions, classes, methods, and YAML keys use *paired* markers —
`# %% [func:name:start]` / `# %% [func:name:end]`, `# %% [class:Foo:start]`,
`# %% [method:Cls.x:start]`, `# %% [top:key:start]`, etc. Simple cells (`# %% [imports]`,
`# %% [module:init]`, `# %% [module:main_guard]`) have a single marker.
Each annotated file opens with a short pointer to this document.

## Step 1 — probe before patching

Before generating any patch payload, run:

```bash
cellsmith status
```

- **If it prints `available`** (exit 0): generate a JSON patch per the schema
  below and save it as `patch.json`. The user (or your tooling) applies it
  with `cellsmith patch patch.json .`.
- **If it errors** (command not found, or exit non-zero): **ignore the cell
  markers entirely.** Edit the target file(s) directly using your normal
  tools per the user's original request. The markers are inert comments — no
  harm in leaving them in place.

## Step 2 — JSON patch shape

```json
{
  "revisions": [
    {
      "filename": "path/to/file.py",
      "revision_type": "CELL_PATCH",
      "cell_id": "method:UserService.validate:start",
      "code_content": "# %% [method:UserService.validate:start]\n    def validate(self, ...):\n        ...\n# %% [method:UserService.validate:end]\n"
    }
  ],
  "changelog": [
    {
      "change_type": "bug_fix",
      "summary": "validate() now rejects empty bearer tokens before hitting the DB.",
      "details": [
        "Replaced is None check with not token.strip()",
        "Added regression test for empty-string token"
      ]
    }
  ]
}
```

### `revisions[]` — surgical edits and file operations

| Field | Required | Notes |
|---|---|---|
| `filename` | yes | Path relative to the patch target dir |
| `revision_type` | yes | `REPLACE` \| `CELL_PATCH` \| `CELL_CREATE` \| `FILE_CREATE` \| `FILE_MOVE` \| `FILE_DELETE` \| `ARCHIVE` |
| `cell_id` | for CELL_PATCH | Must match an existing `# %% [<cell_id>]` marker. For paired cells, target the `:start` marker |
| `code_content` | for patching | For CELL_PATCH: the **complete cell**, beginning with its `:start` marker and including the matching `:end` marker — never a partial diff. For REPLACE / FILE_CREATE: **plain code only** — no markers, no schema header (annotation is applied automatically after a successful patch) |
| `new_filename` | for FILE_MOVE | The destination path |
| `append_after` | optional, CELL_CREATE | An existing cell_id (e.g. `func:top:end`, `method:Cls.run:end`); the new code is inserted immediately after that cell. A `:start` id resolves to its matching `:end`. Omitted: inserts before `module:main_guard`, or at EOF if there is none |

Tool selection (the user pays per token — pick the laconic one):
- `REPLACE` — total rewrites or files under ~50 lines (plain code, no markers)
- `CELL_PATCH` — surgical updates to a specific function/class/method/key
- `CELL_CREATE` — add new logic to an annotated file (plain code, no markers; place with `append_after`)
- `FILE_CREATE` — brand new file from scratch (plain code, no markers)
- `FILE_MOVE` — rename or move a file
- `FILE_DELETE` — delete a file. Use it whenever you mean "remove this
  file"; do not empty a file out instead.
- `ARCHIVE` — the same mechanism, when you mean "retire this, I may want
  it back" rather than "delete this".

### Placement, classes, and nesting

- **Adding a method to a class:** use CELL_CREATE with `append_after` set to
  an existing method of that class (e.g. `"method:UserService.validate:end"`)
  and emit the code **already indented for the class body**. Markers are
  generated automatically after apply.
- **Nested (inner) functions have no markers** and are never patchable on
  their own — they are part of their parent function's cell. To change one,
  CELL_PATCH the parent and emit the parent's complete cell.
- **Prefer nested functions only as a last resort** (true closures over local
  state). When writing new code, default to module-level functions or private
  methods — they get their own cells and stay cheaply patchable. If you
  encounter an existing nested function that doesn't need to be one, propose
  extracting it to the user **once**; if the user declines, respect that
  choice and don't raise it again.
- **`__init__` is a normal method cell:** `method:Cls.__init__:start`.
- Indentation is your responsibility; if it's wrong the file won't parse and
  the patch report will tell you (`Post-check FAILED`), with the previous
  version safe in a backup.

### The patch report

`cellsmith patch` prints a numbered, ordered report — one line per artifact /
revision, `OK` or `FAILED`, plus a corrective instruction for each failure.
Exit codes: `0` all applied, `2` changelog gate rejected (nothing written),
`3` partial — some operations failed, `4` ambiguous destination (nothing
written).

### Ambiguous destinations (exit code 4)

A `cell_id` must resolve to exactly one marker. If the target file carries the
same marker twice — which only happens when a file was edited by hand and its
markers were left inconsistent — the patch is refused **in full, before
anything is written**, rather than guessing which cell you meant. The report
shows each duplicated block and the `cell_id` it will answer to once the
markers are regenerated:

```text
Patch NOT applied - ambiguous destination.

  app.py: cell_id `module:init` matches 2 markers (lines 71, 73).
    [1] line 71  ->  after re-annotate: module:init
           CONST_NEW = 0
    [2] line 73  ->  after re-annotate: module:init
           CONST_A = 1
```

Fix it by regenerating the markers, then re-emit the patch against the ids
shown in the report:

```bash
cellsmith reannotate app.py
```

`cellsmith reannotate` strips and re-marks from the AST, preserving whichever
header variant the file already carries. It is also what `cellsmith patch`
runs automatically on every file it touches, so markers stay aligned after a
successful patch.

**If some revisions fail, re-emit ONLY the FAILED ones.** The OK ones are
already applied; re-sending them wastes tokens and can double-apply appends.
After a successful patch the tool automatically strips and re-annotates every
touched `.py` and `.yaml` file, so markers are always in perfect alignment — you never
need to maintain them by hand.

### `changelog[]` — **BLOCKING GATE**

Every patch payload **must** include at least one changelog entry. Payloads
without a valid `changelog` are rejected by `cellsmith patch` (exit code 2)
before any disk writes happen. Accepted entries are appended to
`CHANGELOG.cellsmith.jsonl` at the patch target root.

| Field | Required | Notes |
|---|---|---|
| `change_type` | yes | One of: `new_feature`, `correcting_implementation`, `bug_fix`, `refactor`, `schema_migration` |
| `summary` | yes | One concise affirmative sentence describing the **final state achieved** — not a recount of past mistakes |
| `details` | no | Array of strings, granular technical bullets |
| `timestamp` | no | ISO-8601 UTC; `cellsmith patch` fills it in at apply time if omitted |
| `author` | no | Free-form model/agent identifier |

## Reading before you patch — `cellsmith read`

Do not read whole files. `cellsmith read` compiles a **dynamic resolution
context**: full code along the execution trace from an entry cell, AST
skeletons one or more layers beyond it, and one-line summaries in the far
background. Emit a JSON request:

```json
{
  "read_request": {
    "entry": "api/routes.py:func:process_payment:start",
    "trace_depth": 2,
    "trace_type": "branching",
    "ast_layers": 2,
    "laconic_background_layers": 1,
    "max_characters": 50000,
    "trace_exclude_paths": ["func:validate_headers"],
    "trace_keep": ["func:_calculate_tax_offset"],
    "include_files": ["docs/payment_flow.md"]
  }
}
```

```bash
cellsmith read read_request.json .
```

| Field | Required | Notes |
|---|---|---|
| `entry` | yes | Focal cell. `file.py:func:name`, with or without `:start` |
| `trace_depth` | no | Call-graph hops rendered at full fidelity. Default 1 |
| `trace_type` | no | `linear` \| `branching` \| `loops` \| `all`. How wide a call site may be to be followed. Default `linear` |
| `ast_layers` | no | Layers past the trace rendered as signature + docstring. Default 1 |
| `laconic_background_layers` | no | Layers past that rendered as one-liners. Default 0 |
| `max_characters` | no | Budget, whitespace excluded. Default 50000 |
| `trace_exclude_paths` | no | Cell ids pruned entirely |
| `trace_keep` | no | Cell ids pinned to full fidelity regardless of depth or budget |
| `include_files` | no | Extra files appended verbatim |

Notes on what you get back:

- **Docstrings are inverted by design.** Full-fidelity cells have theirs
  stripped (you are reading the implementation); AST skeletons keep theirs
  (it is all you get). Write good docstrings — they *are* the summary layer.
- **Class cells render as a shell.** Methods are their own cells and appear
  separately at their own fidelity, so a class body is never duplicated.
- **The budget is evaluated only at cell boundaries**, never mid-cell. A cell
  overrunning by less than 500 characters is committed whole; past that the
  cell is replaced by a `[TRACE_TRUNCATED]` breadcrumb. Re-read with a new
  `entry` to expand it.
- Each file's imports cell is included so what you read stays coherent.

### `post_patch_read` — verify without burning a turn

Add it to a patch payload and `cellsmith patch` compiles the read for you
after a fully successful patch, printing it to stdout:

```json
{
  "revisions": [ ... ],
  "changelog": [ ... ],
  "post_patch_read": {
    "entry": "api/routes.py:func:process_payment",
    "trace_exclude_paths": ["func:validate_headers"]
  }
}
```

It takes the same fields as `read_request`, and is validated up front — a
malformed one rejects the payload before anything is written.

## Telemetry — inspecting what your patch actually did

To see the runtime behaviour of the cells you just patched, set `"telemetry":
true` on the payload (or have the user run `cellsmith patch patch.json .
--trace`). CellSmith wraps each patched cell in a `@focal_trace` decorator and
installs a zero-dependency runtime under `.agents/`.

Run the app or the test suite, then read `.agents/logs/focal_session.jsonl`.
One JSON record per call:

```json
{
  "cell_id": "method:Service.handle",
  "outcome": "raised",
  "args": ["<Service object>", {"a": 3}],
  "kwargs": {"password": "<redacted>"},
  "locals": {"total": 10, "divisor": 0},
  "raised_at_line": 28,
  "exception": {"type": "ZeroDivisionError", "message": "division by zero"},
  "duration_ms": 0.41
}
```

This is a focused stream — only the cells you instrumented appear in it, so
there is nothing to filter out. Values named like credentials are redacted.

Instrumentation is ephemeral and **must not be committed**. When the task is
done:

```bash
cellsmith finalize .
```

That strips every `@focal_trace` decorator and the import preamble. The
decorator lives above the cell's `:start` marker, so it survives your later
patches — keep emitting pure logic and do not write decorators yourself.

### Deleting files is recoverable

`FILE_DELETE` removes the file from the working tree — git reports it as
deleted at the next commit, exactly as if you had removed it. CellSmith keeps
the content in `.cellsmith/archive/` so the delete can be undone before that
commit.

This matters mid-refactor. Git can only take you back to the last commit, so
a plain delete at step 10 of a 15-step refactor means discarding all 15 steps
to get the file back. With `FILE_DELETE`, `cellsmith rollback` on that
payload restores it and leaves the other steps alone.

Deleting the same path more than once in a session is fine. Each version is
kept (`doomed.py`, `doomed.py.1`, ...), so rolling the payloads back in
reverse order restores the right version each time.

## Naming your payload — `patch_name`

Optional, top level, alongside `revisions` and `changelog`:

```json
{
  "patch_name": "0007-fix-payment-rounding.json",
  "revisions": [ ... ],
  "changelog": [ ... ]
}
```

Once applied, `cellsmith patch` moves the payload into `.cellsmith/patches/`
under that name. Omit it and the file keeps the name it arrived with. A
rejected payload is left where it is so you can correct it and retry.

Rolling back works with either name — `cellsmith rollback` looks in
`.cellsmith/patches/` and consults the index that records where each payload
was filed.

## Where CellSmith keeps its own files

```text
.cellsmith/archive/    files retired by ARCHIVE
.cellsmith/backups/    pre-patch copies, used by rollback
.cellsmith/patches/    payloads already applied
CHANGELOG.cellsmith.jsonl   stays at the project root
```

`.cellsmith/` is added to `.gitignore` automatically, and the `.gitignore` is
created if the project has none. Do not patch anything under `.cellsmith/`.

## Step 3 — hand off the JSON

Save the JSON as `patch.json` (or any name) and let the user (or your shell
tool) run:

```bash
cellsmith patch patch.json .
```

If the file changed and the diff isn't what you wanted, roll back:

```bash
cellsmith rollback patch.json .
```

---

*This file is auto-generated by `cellsmith annotate-agent` and regenerated
on every run. Don't edit by hand.*
