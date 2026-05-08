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
# 1. Annotate a file with cell markers + the AI patch schema header
cellsmith annotate path/to/file.py

# 2. Hand the annotated file to your LLM, get back a JSON patch, save it as patch.json
cellsmith patch patch.json .

# 3. Roll back if the patch was bad
cellsmith rollback patch.json .
```

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

Per-revision score is `nodes × tool_multiplier`, summed across the session:

| Tool | Multiplier | Why |
|------|------------|-----|
| `CELL_PATCH` | 1.5× | Surgical — rewards laconic precision |
| `CELL_CREATE` | 1.0× | Standard append |
| `REPLACE` | 0.5× | Brute-force rewrites get penalized |

Honor system on `model` / `engine` (unverifiable). Math is reproducible.

<!-- LB:START -->
| # | Handle | Score | Nodes | 🔧 Patch | ➕ Create | ♻️ Replace | Model | Engine |
|---|--------|-------|-------|---------|----------|------------|-------|--------|
| _no submissions yet — be the first_ | | | | | | | | |
<!-- LB:END -->

## License

MIT
