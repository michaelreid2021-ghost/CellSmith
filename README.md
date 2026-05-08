# CellSmith

A lightweight, schema-driven code patch system that LLMs will love.

CellSmith annotates a Python file with Jupyter-flavored cell markers
(`# %% [func:name]`, `# %% [class:Foo]`, `# %% [method:Cls.x]`, plus protected
`# %% [knobs:*:start]` zones) so an LLM can return surgical JSON patches
against named cells instead of rewriting whole files.

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
```

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
