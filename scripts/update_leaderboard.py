"""Parse a 'high-score' issue, verify the patch, update leaderboard.json + README.

Triggered by .github/workflows/leaderboard.yml. Reads the issue body from env,
extracts the JSON telemetry block and the patched code block, re-runs ast.walk
to confirm the node count, runs `ruff check` on the code, then updates the
leaderboard files. On any verification failure, exits non-zero so the workflow
posts a rejection comment.
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
NODE_TOLERANCE = 2  # absolute slack on claimed node count


def fail(msg: str) -> None:
    Path("action_message.txt").write_text(f"❌ Rejected: {msg}", encoding="utf-8")
    print(msg, file=sys.stderr)
    sys.exit(1)


def succeed(msg: str) -> None:
    Path("action_message.txt").write_text(f"✅ {msg}", encoding="utf-8")
    print(msg)


def extract_block(body: str, lang: str) -> str | None:
    pat = re.compile(rf"```{lang}\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
    m = pat.search(body)
    return m.group(1).strip() if m else None


def extract_field(body: str, header: str) -> str | None:
    # Issue Forms render fields as "### Header\n\nvalue\n\n###"
    pat = re.compile(rf"###\s*{re.escape(header)}\s*\n+(.*?)(?=\n###|\Z)", re.DOTALL)
    m = pat.search(body)
    if not m:
        return None
    val = m.group(1).strip()
    if val.startswith("```"):
        val = re.sub(r"^```\w*\n", "", val)
        val = re.sub(r"\n```$", "", val).strip()
    return val or None


def main() -> None:
    body = os.environ.get("ISSUE_BODY", "")
    user = os.environ.get("ISSUE_USER", "anonymous")
    issue_num = os.environ.get("ISSUE_NUMBER", "?")
    if not body:
        fail("empty issue body")

    handle = extract_field(body, "Display name") or user
    telemetry_raw = extract_field(body, "Telemetry JSON") or extract_block(body, "json")
    code = extract_field(body, "Patched code") or extract_block(body, "python")

    if not telemetry_raw:
        fail("no telemetry JSON found in submission")
    if not code:
        fail("no patched code block found in submission")

    try:
        claimed = json.loads(telemetry_raw)
    except json.JSONDecodeError as e:
        fail(f"telemetry JSON did not parse: {e}")

    tool = claimed.get("tool")
    if tool not in TOOL_MULT:
        fail(f"unknown tool '{tool}' (expected one of {sorted(TOOL_MULT)})")

    try:
        actual_nodes = len(list(ast.walk(ast.parse(code))))
    except SyntaxError as e:
        fail(f"patched code does not parse: {e}")

    claimed_nodes = int(claimed.get("nodes", -1))
    if abs(actual_nodes - claimed_nodes) > NODE_TOLERANCE:
        fail(f"node count mismatch: claimed {claimed_nodes}, recomputed {actual_nodes}")

    # ruff gate
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as tf:
        tf.write(code)
        tmp_path = tf.name
    ruff = subprocess.run(
        ["ruff", "check", tmp_path], capture_output=True, text=True
    )
    os.unlink(tmp_path)
    if ruff.returncode != 0:
        fail(f"ruff check failed:\n```\n{ruff.stdout}{ruff.stderr}\n```")

    score = round(actual_nodes * TOOL_MULT[tool], 2)
    entry = {
        "rank": None,
        "handle": handle,
        "score": score,
        "nodes": actual_nodes,
        "tool": tool,
        "model": claimed.get("model", "unknown"),
        "engine": claimed.get("engine", "unknown"),
        "ts": claimed.get("ts", ""),
        "issue": int(issue_num) if str(issue_num).isdigit() else issue_num,
    }

    board = json.loads(LEADERBOARD.read_text(encoding="utf-8")) if LEADERBOARD.exists() else []
    board.append(entry)
    board.sort(key=lambda e: e["score"], reverse=True)
    board = board[:50]
    for i, e in enumerate(board, 1):
        e["rank"] = i

    LEADERBOARD.write_text(json.dumps(board, indent=2) + "\n", encoding="utf-8")

    rows = ["| # | Handle | Score | Nodes | Tool | Model | Engine |",
            "|---|--------|-------|-------|------|-------|--------|"]
    for e in board:
        rows.append(
            f"| {e['rank']} | {e['handle']} | {e['score']} | {e['nodes']} | "
            f"{e['tool']} | {e['model']} | {e['engine']} |"
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

    rank = next(e["rank"] for e in board if e is entry or (e["handle"] == handle and e["score"] == score))
    succeed(f"Verified! **{handle}** ranked **#{rank}** with score **{score}** "
            f"({actual_nodes} nodes × {TOOL_MULT[tool]} {tool}).")


if __name__ == "__main__":
    main()
