"""Parse a 'high-score' issue, verify the entire patch session, update files.

Triggered by .github/workflows/leaderboard.yml. Reads the issue body from env,
extracts the JSON patch payload (revisions[]), then for each revision re-runs
ast.walk on code_content and runs ruff check. Sums a weighted session score
and tallies counts per tool. Updates leaderboard.json + README table. On any
verification failure, exits non-zero so the workflow posts a rejection comment.
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LEADERBOARD = REPO_ROOT / "leaderboard.json"
README = REPO_ROOT / "README.md"
LB_START = "<!-- LB:START -->"
LB_END = "<!-- LB:END -->"
TOOL_MULT = {"CELL_PATCH": 1.5, "CELL_CREATE": 1.0, "REPLACE": 0.5}


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
    if val.startswith("```"):
        val = re.sub(r"^```\w*\n", "", val)
        val = re.sub(r"\n```$", "", val).strip()
    return val or None


def ruff_ok(code: str) -> tuple[bool, str]:
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as tf:
        tf.write(code)
        tmp_path = tf.name
    res = subprocess.run(["ruff", "check", tmp_path], capture_output=True, text=True)
    os.unlink(tmp_path)
    return res.returncode == 0, res.stdout + res.stderr


def main() -> None:
    body = os.environ.get("ISSUE_BODY", "")
    user = os.environ.get("ISSUE_USER", "anonymous")
    issue_num = os.environ.get("ISSUE_NUMBER", "?")
    if not body:
        fail("empty issue body")

    handle = extract_field(body, "Display name") or user
    model = extract_field(body, "Model") or "unknown"
    engine = extract_field(body, "Engine") or "unknown"
    input_src = extract_field(body, "Input context (source the LLM reviewed)")
    if not input_src:
        fail("no input context provided")
    try:
        input_nodes = len(list(ast.walk(ast.parse(input_src))))
    except SyntaxError as e:
        fail(f"input context did not parse as Python: {e}")
    if input_nodes == 0:
        fail("input context parsed to zero AST nodes")

    payload_raw = extract_field(body, "JSON patch payload")
    if not payload_raw:
        fail("no JSON patch payload found")

    try:
        payload = json.loads(payload_raw)
    except json.JSONDecodeError as e:
        fail(f"payload JSON did not parse: {e}")

    revisions = payload.get("revisions", []) or []
    if not revisions:
        fail("payload has no `revisions` array (or it's empty)")

    counts = {"CELL_PATCH": 0, "CELL_CREATE": 0, "REPLACE": 0}
    total_nodes = 0
    total_score = 0.0
    rejections = []

    for i, rev in enumerate(revisions):
        tool = rev.get("revision_type")
        code = rev.get("code_content", "")
        if tool not in TOOL_MULT:
            rejections.append(f"revision[{i}]: unknown tool '{tool}'")
            continue
        if not code.strip():
            rejections.append(f"revision[{i}]: empty code_content")
            continue
        try:
            nodes = len(list(ast.walk(ast.parse(code))))
        except SyntaxError as e:
            rejections.append(f"revision[{i}] ({tool}): syntax error: {e}")
            continue
        ok, ruff_out = ruff_ok(code)
        if not ok:
            snippet = ruff_out.strip().splitlines()[:5]
            rejections.append(f"revision[{i}] ({tool}): ruff failed:\n" + "\n".join(snippet))
            continue
        counts[tool] += 1
        total_nodes += nodes
        total_score += nodes * TOOL_MULT[tool]

    if rejections:
        fail("one or more revisions failed verification:\n\n" + "\n\n".join(rejections))

    total_score = round(total_score, 2)
    leverage = round(total_score / input_nodes, 4) if input_nodes else 0.0
    entry = {
        "rank": None,
        "handle": handle,
        "score": total_score,
        "nodes": total_nodes,
        "input_nodes": input_nodes,
        "leverage": leverage,
        "patches": counts["CELL_PATCH"],
        "creates": counts["CELL_CREATE"],
        "replaces": counts["REPLACE"],
        "revisions": len(revisions),
        "model": model,
        "engine": engine,
        "issue": int(issue_num) if str(issue_num).isdigit() else issue_num,
    }

    board = json.loads(LEADERBOARD.read_text(encoding="utf-8")) if LEADERBOARD.exists() else []
    board.append(entry)
    board.sort(key=lambda e: e["score"], reverse=True)
    board = board[:50]
    for i, e in enumerate(board, 1):
        e["rank"] = i

    LEADERBOARD.write_text(json.dumps(board, indent=2) + "\n", encoding="utf-8")

    rows = [
        "| # | Handle | Score | Out Nodes | In Nodes | Leverage | 🔧 Patch | ➕ Create | ♻️ Replace | Model | Engine |",
        "|---|--------|-------|-----------|----------|----------|---------|----------|------------|-------|--------|",
    ]
    for e in board:
        rows.append(
            f"| {e['rank']} | {e['handle']} | {e['score']} | {e['nodes']} | "
            f"{e.get('input_nodes', '?')} | {e.get('leverage', '?')} | "
            f"{e['patches']} | {e['creates']} | {e['replaces']} | "
            f"{e['model']} | {e['engine']} |"
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
    succeed(
        f"Verified! **{handle}** ranked **#{rank}** with score **{total_score}** "
        f"across {len(revisions)} revision(s) — "
        f"{counts['CELL_PATCH']}× CELL_PATCH, "
        f"{counts['CELL_CREATE']}× CELL_CREATE, "
        f"{counts['REPLACE']}× REPLACE. "
        f"Output: {total_nodes} nodes from an input context of {input_nodes} nodes "
        f"(leverage {leverage})."
    )


if __name__ == "__main__":
    main()
