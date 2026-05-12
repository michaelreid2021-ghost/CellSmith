"""`cellsmith submit` — open a pre-filled GitHub issue in the browser.

Reads the patch JSON locally, computes score/nodes/tool-counts right here,
counts chars in any --context files, then opens a GitHub issue form with
all the numbers pre-filled. Nothing but integers and short strings go in
the URL. No code, no source, nothing suss.
"""
from __future__ import annotations

import ast
import json
import logging
import os
import subprocess
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Iterable

DEFAULT_REPO = "michaelreid2021-ghost/CellSmith"
TEMPLATE = "high-score.yml"

CATEGORY_LABEL = {
    "tiny": "<4B",
    "small": "<10B",
    "medium": "<30B",
    "frontier": "frontier",
    "unknown": "unknown",
}

TOOL_MULTIPLIER = {"CELL_PATCH": 1.5, "CELL_CREATE": 1.0, "REPLACE": 0.5}


def _score_payload(payload_path: Path) -> dict:
    """Read the patch JSON and compute session stats locally. No code leaves."""
    try:
        data = json.loads(payload_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logging.error(f"submit: could not read payload: {e}")
        return {}

    revisions = data.get("revisions", [])
    counts = {"CELL_PATCH": 0, "CELL_CREATE": 0, "REPLACE": 0}
    total_nodes = 0
    total_score = 0.0

    for rev in revisions:
        tool = rev.get("revision_type")
        code = rev.get("code_content", "")
        if tool not in TOOL_MULTIPLIER or not code.strip():
            continue
        try:
            nodes = len(list(ast.walk(ast.parse(code))))
        except SyntaxError:
            nodes = 0
        counts[tool] = counts.get(tool, 0) + 1
        total_nodes += nodes
        total_score += nodes * TOOL_MULTIPLIER[tool]

    return {
        "score": round(total_score, 2),
        "nodes": total_nodes,
        "patches": counts["CELL_PATCH"],
        "creates": counts["CELL_CREATE"],
        "replaces": counts["REPLACE"],
        "revisions": len(revisions),
    }


def _lint_patched_files(payload_path: Path, target_dir: Path) -> tuple[bool, list[str]]:
    """Run ruff on every Python file touched by the patch. Returns (passed, issues)."""
    try:
        data = json.loads(payload_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, ["could not read payload"]

    touched = {
        target_dir / rev["filename"]
        for rev in data.get("revisions", [])
        if rev.get("filename", "").endswith(".py")
    }
    touched = {p for p in touched if p.exists()}
    if not touched:
        return True, []

    try:
        result = subprocess.run(
            ["ruff", "check"] + [str(p) for p in sorted(touched)],
            capture_output=True, text=True,
        )
        issues = [l for l in (result.stdout + result.stderr).splitlines() if l.strip()]
        return result.returncode == 0, issues
    except FileNotFoundError:
        return False, ["ruff not installed — run: pip install ruff"]


def _count_input_chars(context_paths: Iterable[Path], iter_python_files) -> int:
    """Count total characters across context files. Source stays on disk."""
    total = 0
    for root in context_paths:
        for f in iter_python_files(root):
            try:
                total += len(f.read_text(encoding="utf-8"))
            except OSError as e:
                logging.warning(f"submit: could not read {f}: {e}")
    return total


def build_url(
    repo: str,
    *,
    handle: str,
    model: str,
    engine: str,
    score: float,
    nodes: int,
    input_chars: int,
    patches: int,
    creates: int,
    replaces: int,
    revisions: int,
    lint_passed: bool,
    category: str | None = None,
    notes: str | None = None,
) -> str:
    params = {
        "template": TEMPLATE,
        "labels": "high-score",
        "handle": handle,
        "model": model,
        "engine": engine,
        "score": str(score),
        "nodes": str(nodes),
        "input_chars": str(input_chars),
        "patches": str(patches),
        "creates": str(creates),
        "replaces": str(replaces),
        "revisions": str(revisions),
        "lint_passed": "yes" if lint_passed else "no",
    }
    if category:
        params["category"] = CATEGORY_LABEL.get(category, category)
    if notes:
        params["prompt_notes"] = notes
    qs = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    return f"https://github.com/{repo}/issues/new?{qs}"


def _open_url(url: str) -> bool:
    """Open url in the default browser.

    Windows webbrowser.open() calls os.startfile() which chokes on long URLs.
    Fall back to a local meta-refresh HTML stub with a short file:// path.
    """
    import tempfile
    import threading

    try:
        return webbrowser.open(url)
    except (ValueError, OSError):
        pass

    escaped = url.replace("&", "&amp;").replace('"', "&quot;")
    html = (
        "<!DOCTYPE html><html><head>"
        f'<meta http-equiv="refresh" content="0;url={escaped}">'
        "<title>Redirecting to GitHub…</title></head><body>"
        f'<p>Opening GitHub… <a href="{escaped}">click here</a> if not redirected.</p>'
        "</body></html>\n"
    )
    try:
        tf = tempfile.NamedTemporaryFile(
            "w", suffix=".html", delete=False, encoding="utf-8",
            prefix="cellsmith_submit_",
        )
        tf.write(html)
        tf.flush()
        tf.close()
        tmp_path = tf.name
        opened = webbrowser.open(f"file:///{tmp_path}")

        def _cleanup():
            import time
            import os as _os
            time.sleep(10)
            try:
                _os.unlink(tmp_path)
            except OSError:
                pass

        threading.Thread(target=_cleanup, daemon=True).start()
        return opened
    except Exception:
        return False


def run_submit(args, iter_python_files) -> int:
    """Entry point called from markup.py main(). Returns process exit code."""
    repo = os.environ.get("CELLSMITH_REPO", DEFAULT_REPO)

    payload_path: Path = args.payload
    if not payload_path.exists():
        logging.error(f"payload not found: {payload_path}")
        return 1

    stats = _score_payload(payload_path)
    if not stats:
        return 1

    # Lint the patched files locally — result travels as a flag, not code.
    target_dir = Path(args.target_dir) if hasattr(args, "target_dir") and args.target_dir else Path(".")
    lint_ok, lint_issues = _lint_patched_files(payload_path, target_dir)
    if lint_ok:
        print("✅ ruff lint passed")
    else:
        print("⚠️  ruff lint issues found (submitting anyway — flag will show on leaderboard):")
        for line in lint_issues[:10]:
            print(f"   {line}")

    input_chars = 0
    if args.context:
        input_chars = _count_input_chars(args.context, iter_python_files)
    else:
        # Fall back to counting chars from the files named in the patch itself.
        try:
            data = json.loads(payload_path.read_text(encoding="utf-8"))
            touched = {
                target_dir / rev["filename"]
                for rev in data.get("revisions", [])
                if rev.get("filename")
            }
            for p in touched:
                if p.exists():
                    try:
                        input_chars += len(p.read_text(encoding="utf-8"))
                    except OSError:
                        pass
            if input_chars:
                print(f"Input context: {input_chars:,} chars (auto-detected from patched files)")
        except (OSError, json.JSONDecodeError):
            pass

    leverage = round(stats["score"] / input_chars * 1000, 2) if input_chars else 0.0

    print(
        f"\nSession: {stats['revisions']} revision(s) — "
        f"{stats['patches']}× CELL_PATCH, {stats['creates']}× CELL_CREATE, {stats['replaces']}× REPLACE\n"
        f"Score:   {stats['score']}  |  Nodes: {stats['nodes']}  |  "
        f"Input: {input_chars:,} chars  |  Leverage: {leverage}"
    )

    url = build_url(
        repo,
        handle=args.handle,
        model=args.model,
        engine=args.engine,
        score=stats["score"],
        nodes=stats["nodes"],
        input_chars=input_chars,
        patches=stats["patches"],
        creates=stats["creates"],
        replaces=stats["replaces"],
        revisions=stats["revisions"],
        lint_passed=lint_ok,
        category=args.category,
        notes=args.notes,
    )

    if args.print_only:
        print(url)
        return 0

    print(f"\nOpening issue form on {repo}…")
    if not _open_url(url):
        print("Could not auto-open browser. Open this URL manually:\n")
        print(url)
    else:
        print("Browser opened. Everything is pre-filled — just hit Submit.")
    return 0
