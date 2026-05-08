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

## Why a leaderboard?

Because this is the kind of tooling whose ceiling is set by **prompting and
model choice**, not by the framework. There's no "official" right way to
prompt an LLM into landing a 74-revision payload — and the surprising answers
(maybe a 4B local model with a clever skeleton trick beats a frontier model
with a lazy prompt) are the *interesting* answers.

The leaderboard makes those answers visible. The scoring is intentionally
passive (the LLM never sees the metric — see `src/cellsmith/telemetry.py`
docstring, then re-read [Goodhart](https://en.wikipedia.org/wiki/Goodhart%27s_law)
and never tell it). What gets ranked is what the human + model pair actually
landed: how dense, how surgical, against how much input context.

If a 4B model with a hand-crafted skeleton tops `patch_tuesday.json`'s score,
that's worth knowing. If frontier models always win, that's worth knowing too.

## Install

```bash
pip install -e .
```

## Use

```bash
# 1a. Annotate a single file
cellsmith annotate path/to/file.py

# 1b. Or annotate every .py in a project — respects .gitignore by default,
#     skips dotted dirs (.git/.venv) and dunder dirs (__pycache__).
cellsmith annotate .                  # full project
cellsmith annotate . --dry-run        # preview which files would be touched
cellsmith annotate . --no-gitignore   # ignore .gitignore filtering
cellsmith annotate . --include-hidden # include dotted dirs/files

# 2. Hand annotated file(s) to your LLM, get back a JSON patch, save as patch.json
cellsmith patch patch.json .

# 3. Roll back if the patch was bad
cellsmith rollback patch.json .

# 4. When you're done, strip cell markers + schema header to get plain code back
cellsmith strip path/to/file.py                # asks for confirmation
cellsmith strip . -y                           # whole project, skip prompt
cellsmith strip path/to/file.py --prompt-only  # keep markers, drop schema only
cellsmith strip path/to/file.py --markers-only # keep schema, drop markers only
```

👉 **[examples/hello_world/WALKTHROUGH.md](examples/hello_world/WALKTHROUGH.md)** —
end-to-end tour: annotate a trivial file, ask any chat-UI LLM to make it
"the most complex Hello World imaginable" (no schema explanation needed —
the file teaches the model), apply the patch, optionally submit to the
leaderboard.

> Annotation is Python-only (it walks the AST). Patching can target **any**
> file via `REPLACE` (JSON, TOML, Markdown, anything), and `CELL_PATCH` /
> `CELL_CREATE` work on any file that has cell markers — but for now only
> Python files get marker generation out of the box.

Every patch run silently appends a row to `patch_telemetry.jsonl` in the
working directory — your private brag log. Set `CELLSMITH_MODEL` and
`CELLSMITH_ENGINE` env vars before running so the telemetry knows which model
got the credit.

## 🏆 Leaderboard

Click **Issues → New issue → 🏆 Submit High Score** and paste your full JSON
patch payload (the one you fed to `cellsmith patch`). A GitHub Action iterates
every revision, re-runs `ast.walk` on each `code_content`, runs `ruff check`
on each, and ranks you by the **summed weighted score across the whole
session**. You'll get tallies of how many CELL_PATCH / CELL_CREATE / REPLACE
ops your LLM landed in one shot. Cheaters and broken code get rejected with
a comment.

Per-revision score is `nodes × tool_multiplier`, summed across the session.
We also count the **input context** (the source you handed the LLM) and show
**Leverage** = `output_score / input_nodes` — i.e. how much logic the model
produced per node of context it had to comprehend. Tiny model + huge codebase
+ surgical CELL_PATCH = absurdly high leverage.

| Tool | Multiplier | Why |
|------|------------|-----|
| `CELL_PATCH` | 1.5× | Surgical — rewards laconic precision |
| `CELL_CREATE` | 1.0× | Standard append |
| `REPLACE` | 0.5× | Brute-force rewrites get penalized |

Honor system on `model` / `engine` (unverifiable). Math is reproducible.

<!-- LB:START -->
| # | Handle | Score | Out Nodes | In Nodes | Leverage | 🔧 Patch | ➕ Create | ♻️ Replace | Model | Engine |
|---|--------|-------|-----------|----------|----------|---------|----------|------------|-------|--------|
| _no submissions yet — be the first_ | | | | | | | | | | |
<!-- LB:END -->

## License

MIT
