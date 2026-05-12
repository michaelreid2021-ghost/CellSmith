"""Parse a 'high-score' issue and update the leaderboard.

Triggered by .github/workflows/leaderboard.yml. Reads the pre-computed
session stats from the issue body (score, nodes, tool counts, input_chars)
and inserts them into leaderboard.json + README table. Honor system —
same trust level as the model/engine fields.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LEADERBOARD = REPO_ROOT / "leaderboard.json"
README = REPO_ROOT / "README.md"
LB_START = "<!-- LB:START -->"
LB_END = "<!-- LB:END -->"

MODEL_HASHTAGS = [
    ("claude", "#Anthropic #Claude"),
    ("gemma", "#GoogleDeepMind #Gemma"),
    ("gemini", "#GoogleDeepMind #Gemini"),
    ("llama", "#MetaAI #Llama"),
    ("qwen", "#Alibaba #Qwen"),
    ("mistral", "#MistralAI"),
    ("phi", "#Microsoft #Phi"),
    ("deepseek", "#DeepSeek"),
    ("gpt", "#OpenAI"),
    ("o1", "#OpenAI"),
    ("o3", "#OpenAI"),
    ("o4", "#OpenAI"),
]


def model_hashtags(model: str) -> str:
    m = model.lower()
    for needle, tags in MODEL_HASHTAGS:
        if needle in m:
            return tags
    return "#OpenSourceLLM"


def write_tweet(*, handle, model, engine, score, leverage, patches, creates,
                replaces, rank, rank_in_cat, category, is_pb, issue_num) -> None:
    repo = os.environ.get("GITHUB_REPOSITORY", "michaelreid2021-ghost/CellSmith")
    issue_url = f"https://github.com/{repo}/issues/{issue_num}"
    pb_prefix = "🏆 PERSONAL BEST! " if is_pb else "🔥 New entry: "
    cat_line = f"\n→ #{rank_in_cat} in {category} tier" if category and category != "unknown" else ""
    ops = []
    if patches:
        ops.append(f"{patches}× CELL_PATCH")
    if creates:
        ops.append(f"{creates}× CELL_CREATE")
    if replaces:
        ops.append(f"{replaces}× REPLACE")
    ops_line = " + ".join(ops) if ops else "0 ops"

    tweet = (
        f"{pb_prefix}{handle} on CellSmith\n"
        f"{model} ({engine}) landed {ops_line}\n"
        f"→ score {score} · leverage {leverage}\n"
        f"→ #{rank} all-time{cat_line}\n\n"
        f"{model_hashtags(model)} #CellSmith #LLM\n"
        f"{issue_url}"
    )
    if len(tweet) > 280:
        tweet = tweet[:277] + "…"
    Path("tweet_text.txt").write_text(tweet, encoding="utf-8")


def fail(msg: str) -> None:
    Path("action_message.txt").write_text(f"❌ Rejected: {msg}", encoding="utf-8")
    print(msg, file=sys.stderr)
    sys.exit(1)


def succeed(msg: str) -> None:
    Path("action_message.txt").write_text(f"✅ {msg}", encoding="utf-8")
    print(msg)


def extract_field(body: str, header: str) -> str | None:
    pat = re.compile(rf"###\s*{re.escape(header)}\s*\n+(.*?)(?=\n###|\Z)", re.DOTALL)
    m = pat.search(body)
    if not m:
        return None
    val = m.group(1).strip()
    return val or None


def parse_int(body: str, header: str, default: int = 0) -> int:
    raw = extract_field(body, header) or str(default)
    try:
        return int(raw.strip().replace(",", ""))
    except ValueError:
        return default


def parse_float(body: str, header: str, default: float = 0.0) -> float:
    raw = extract_field(body, header) or str(default)
    try:
        return float(raw.strip().replace(",", ""))
    except ValueError:
        return default


def main() -> None:
    body = os.environ.get("ISSUE_BODY", "")
    user = os.environ.get("ISSUE_USER", "anonymous")
    issue_num = os.environ.get("ISSUE_NUMBER", "?")
    if not body:
        fail("empty issue body")

    handle = extract_field(body, "Display name") or user
    model = extract_field(body, "Model") or "unknown"
    engine = extract_field(body, "Engine") or "unknown"
    category = extract_field(body, "Model size category (self-declared, honor system)") or "unknown"
    if category not in ("<4B", "<10B", "<30B", "frontier", "unknown"):
        category = "unknown"

    score = parse_float(body, "Score")
    nodes = parse_int(body, "Output nodes")
    input_chars = parse_int(body, "Input context size (characters)")
    patches = parse_int(body, "CELL_PATCH count")
    creates = parse_int(body, "CELL_CREATE count")
    replaces = parse_int(body, "REPLACE count")
    revisions = parse_int(body, "Total revisions")

    lint_raw = extract_field(body, "Ruff lint passed (local check)") or "no"
    lint_passed = lint_raw.strip().lower() == "yes"

    if score <= 0:
        fail("score must be greater than zero")
    if revisions <= 0:
        fail("total revisions must be greater than zero")

    leverage = round(score / input_chars * 1000, 4) if input_chars else 0.0

    board = json.loads(LEADERBOARD.read_text(encoding="utf-8")) if LEADERBOARD.exists() else []
    prior_pb = max((e["score"] for e in board if e.get("handle") == handle), default=0.0)
    is_personal_best = score > prior_pb

    entry = {
        "rank": None,
        "rank_in_category": None,
        "handle": handle,
        "score": score,
        "nodes": nodes,
        "input_chars": input_chars,
        "leverage": leverage,
        "patches": patches,
        "creates": creates,
        "replaces": replaces,
        "revisions": revisions,
        "model": model,
        "engine": engine,
        "category": category,
        "lint_passed": lint_passed,
        "personal_best": is_personal_best,
        "issue": int(issue_num) if str(issue_num).isdigit() else issue_num,
    }

    board.append(entry)
    board.sort(key=lambda e: e["score"], reverse=True)
    board = board[:50]
    for i, e in enumerate(board, 1):
        e["rank"] = i

    cat_counter: dict[str, int] = {}
    for e in board:
        c = e.get("category", "unknown")
        cat_counter[c] = cat_counter.get(c, 0) + 1
        e["rank_in_category"] = cat_counter[c]

    LEADERBOARD.write_text(json.dumps(board, indent=2) + "\n", encoding="utf-8")

    rows = [
        "| # | Handle | Score | Nodes | In (chars) | Leverage | 🔧 | ➕ | ♻️ | Lint | Tier | Model | Engine |",
        "|---|--------|-------|-------|------------|----------|----|----|----|------|------|-------|--------|",
    ]
    for e in board:
        cat_rank = e.get("rank_in_category")
        cat = e.get("category", "unknown")
        cat_cell = f"#{cat_rank} {cat}" if cat_rank else cat
        rows.append(
            f"| {e['rank']} | {e['handle']} | {e['score']} | {e['nodes']} | "
            f"{e.get('input_chars', '?')} | {e.get('leverage', '?')} | "
            f"{e['patches']} | {e['creates']} | {e['replaces']} | "
            f"{'✅' if e.get('lint_passed') else '⚠️'} | "
            f"{cat_cell} | {e['model']} | {e['engine']} |"
        )
    table = "\n".join(rows)

    readme = README.read_text(encoding="utf-8")
    if LB_START in readme and LB_END in readme:
        readme = re.sub(
            rf"{re.escape(LB_START)}.*?{re.escape(LB_END)}",
            f"{LB_START}\n{table}\n{LB_END}",
            readme,
            flags=re.DOTALL,
        )
    else:
        readme += f"\n\n## 🏆 Leaderboard\n\n{LB_START}\n{table}\n{LB_END}\n"
    README.write_text(readme, encoding="utf-8")

    rank = next(e["rank"] for e in board if e["issue"] == entry["issue"])
    rank_in_cat = next(e["rank_in_category"] for e in board if e["issue"] == entry["issue"])

    write_tweet(
        handle=handle, model=model, engine=engine, score=score,
        leverage=leverage, patches=patches, creates=creates, replaces=replaces,
        rank=rank, rank_in_cat=rank_in_cat, category=category,
        is_pb=is_personal_best, issue_num=issue_num,
    )

    succeed(
        f"**{handle}** ranked **#{rank}** with score **{score}** "
        f"across {revisions} revision(s) — "
        f"{patches}× CELL_PATCH, {creates}× CELL_CREATE, {replaces}× REPLACE. "
        f"{nodes} output nodes, {input_chars:,} input chars, leverage {leverage}."
    )


if __name__ == "__main__":
    main()
