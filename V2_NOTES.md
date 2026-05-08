# V2 Notes — Things to revisit

Stuff deliberately punted from v1. Add to as you go.

## Leaderboard

- **Leverage cheating.** Submitters paste their own "input context" — nothing
  stops someone pasting a tiny snippet to inflate `output_score / input_nodes`.
  Mitigation ideas: (a) require pasting the LLM's user prompt as evidence;
  (b) add a separate "verified leverage" category that requires a public repo
  URL the Action can clone and AST-walk for itself; (c) require minimum input
  size for any podium spot. **Posture for now:** controversy = engagement.
  Let it ride; iterate when there's actual reach.

- **Pytest "verified" bonus tier.** `× 1.25` multiplier when the submitter
  attaches a passing pytest run. Adds a "Verified" badge column.

- **Per-model / per-engine sub-leaderboards.** Filter views by model class
  (e.g. ≤4B params, ≤8B, frontier). Lets local-model brags compete fairly.

- **Weekly resets / seasons.** Top 50 all-time + top 10 this week.

## Annotate

- **Multi-language patching.** `markup.py` handles Python AST. JSON / TOML
  files can already be `REPLACE`d wholesale. v2: light cell markers for JSON
  (object-key cells) and Markdown (heading cells) so CELL_PATCH can target
  non-Python files surgically.

- **Nested .gitignore.** Currently only honors the `.gitignore` at the
  annotate root. Real repos have nested ones. Use `pathspec`'s file-by-file
  walk if it becomes painful.

- **Custom ignore patterns.** `--ignore "tests/**"` flag.
