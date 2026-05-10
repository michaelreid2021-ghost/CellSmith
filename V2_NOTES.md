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

## X auto-posting

- **Image cards.** Generate a PNG of the new leaderboard row (matplotlib or
  pillow) and attach it as media via `tweepy.API.media_upload` →
  `client.create_tweet(media_ids=[...])`. Way higher engagement than text-only.
- **Reply-thread for transparency.** Auto-reply to the main tweet with the
  trimmed JSON payload + the leverage math, so anyone curious can audit it
  without leaving X.
- **Rate limiting / quiet hours.** If submissions spike, batch them or skip
  posts between e.g. 02:00–06:00 in the project's chosen timezone.
- **Curated `models.json`.** Auto-categorize models by name pattern instead
  of self-declaration, once we know which models actually show up.

## Versioning / mini-state / `--accept`

Today's `rollback` reverts a single `.bak`. That breaks down for the realistic
LLM workflow: you submit patch A, the LLM submits patch B that fixes a bug
*introduced by* patch A, then patch C which is a polish pass. Rolling back
just C leaves you with B's mid-fix state — useless.

**v2 design (sketch):**

- Replace `*.bak` with a per-file **revision stack** under
  `.cellsmith/state/<file-hash>/v0001`, `v0002`, ... — never overwritten.
- `cellsmith patch p.json` pushes a new revision onto every touched file's
  stack and writes a **session manifest** (`.cellsmith/sessions/<ts>.json`)
  recording which patch JSON produced which revisions.
- `cellsmith rollback` reverts the **current session** (all files together),
  not a single file.
- `cellsmith accept` locks in the current chain — collapses the revision
  stack into a single committed snapshot and prunes prior revisions for
  those files. Optional flag `--squash N` to keep the last N revisions
  before the accept point.
- `cellsmith log <file>` shows the revision stack + session it came from
  (like `git log` for a single file's patch history).
- `cellsmith diff <fileA> --rev v0003` to inspect any prior revision.

This makes the LLM-iterative loop sane: you can accept *the series* once
the final patch resolves, not gamble on a single rollback.

## Annotate

- **Multi-language patching.** `markup.py` handles Python AST. JSON / TOML
  files can already be `REPLACE`d wholesale. v2: light cell markers for JSON
  (object-key cells) and Markdown (heading cells) so CELL_PATCH can target
  non-Python files surgically.

- **Nested .gitignore.** Currently only honors the `.gitignore` at the
  annotate root. Real repos have nested ones. Use `pathspec`'s file-by-file
  walk if it becomes painful.

- **Custom ignore patterns.** `--ignore "tests/**"` flag.
