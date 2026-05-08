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

Click **Issues → New issue → 🏆 Submit High Score** and paste one line from
your `patch_telemetry.jsonl` plus the patched code. A GitHub Action re-runs
`ast.walk` to verify the node count, runs `ruff check` on the code, and
updates the table below automatically. Cheaters get rejected with a comment.

The score is `nodes × tool_multiplier`:

| Tool | Multiplier | Why |
|------|------------|-----|
| `CELL_PATCH` | 1.5× | Surgical — rewards laconic precision |
| `CELL_CREATE` | 1.0× | Standard append |
| `REPLACE` | 0.5× | Brute-force rewrites get penalized |

Honor system on `model` / `engine` (unverifiable). Math is reproducible.

<!-- LB:START -->
| # | Handle | Score | Nodes | Tool | Model | Engine |
|---|--------|-------|-------|------|-------|--------|
| _no submissions yet — be the first_ | | | | | | |
<!-- LB:END -->

## License

MIT
