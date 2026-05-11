"""`cellsmith submit` — open a pre-filled GitHub issue in the browser.

Reduces the leaderboard-submission flow to one shell command. Reads your
patch JSON + your annotated input context, URL-encodes them into GitHub's
issue-form prefill query string, and opens the result. Click submit. Done.

Falls back to printing the URL if the browser can't open or if --print-only.
"""
from __future__ import annotations

import logging
import os
import sys
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Iterable

DEFAULT_REPO = "michaelreid2021-ghost/CellSmith"
TEMPLATE = "high-score.yml"

# CLI-friendly category values map to display labels (shell-safe; no `<` glyph
# that some shells parse as redirect).
CATEGORY_LABEL = {
    "tiny": "<4B",
    "small": "<10B",
    "medium": "<30B",
    "frontier": "frontier",
    "unknown": "unknown",
}
# GitHub URL practical max ~8 KB. We cap context paste size below that.
MAX_CONTEXT_BYTES = 60_000
MAX_PAYLOAD_BYTES = 60_000


def _gather_context(context_paths: Iterable[Path], iter_python_files) -> str:
    """Concatenate Python files under each given path with `# === <path> ===` headers."""
    blobs = []
    for root in context_paths:
        for f in iter_python_files(root):
            try:
                content = f.read_text(encoding="utf-8")
            except OSError as e:
                logging.warning(f"submit: could not read {f}: {e}")
                continue
            blobs.append(f"# === {f} ===\n{content}")
    return "\n\n".join(blobs)


def build_url(
    repo: str,
    *,
    handle: str,
    model: str,
    engine: str,
    payload: str,
    input_context: str,
    category: str | None = None,
    notes: str | None = None,
) -> str:
    params = {
        "template": TEMPLATE,
        "labels": "high-score",
        "handle": handle,
        "model": model,
        "engine": engine,
        "payload": payload,
        "input_context": input_context,
    }
    if category:
        params["category"] = CATEGORY_LABEL.get(category, category)
    if notes:
        params["prompt_notes"] = notes
    qs = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    return f"https://github.com/{repo}/issues/new?{qs}"


def _open_url(url: str) -> bool:
    """Open *url* in the default browser, working around Windows os.startfile
    path-length limits by bouncing through a tiny local HTML redirect file."""
    import tempfile
    import threading

    # Try direct open first (works fine on macOS/Linux and for short URLs).
    try:
        return webbrowser.open(url)
    except (ValueError, OSError):
        pass  # Windows path-too-long or similar — fall through to HTML bounce.

    # Write a self-refreshing HTML stub and open its (short) local path.
    escaped = url.replace("&", "&amp;").replace('"', "&quot;")
    html = (
        "<!DOCTYPE html><html><head>"
        f'<meta http-equiv="refresh" content="0;url={escaped}">'
        f'<title>Redirecting…</title></head><body>'
        f'<p>If you are not redirected automatically, '
        f'<a href="{escaped}">click here</a>.</p>'
        "</body></html>\n"
    )
    try:
        tf = tempfile.NamedTemporaryFile(
            "w", suffix=".html", delete=False, encoding="utf-8",
            prefix="cellsmith_submit_"
        )
        tf.write(html)
        tf.flush()
        tf.close()
        tmp_path = tf.name

        opened = webbrowser.open(f"file:///{tmp_path}")

        # Clean up the temp file after a short delay so the browser has time
        # to read it before it disappears.
        def _cleanup():
            import time, os as _os
            time.sleep(5)
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
    payload = payload_path.read_text(encoding="utf-8")
    if len(payload.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        logging.warning(
            f"payload is {len(payload)} bytes — over {MAX_PAYLOAD_BYTES} URL cap. "
            "Opening blank issue form; paste the payload manually."
        )
        payload = ""

    context_paths = args.context or []
    input_context = ""
    if context_paths:
        input_context = _gather_context(context_paths, iter_python_files)
        if len(input_context.encode("utf-8")) > MAX_CONTEXT_BYTES:
            logging.warning(
                f"input context is {len(input_context)} bytes — over "
                f"{MAX_CONTEXT_BYTES} URL cap. Opening form without it; paste manually."
            )
            input_context = ""

    url = build_url(
        repo,
        handle=args.handle,
        model=args.model,
        engine=args.engine,
        payload=payload,
        input_context=input_context,
        category=args.category,
        notes=args.notes,
    )

    if args.print_only:
        print(url)
        return 0

    print(f"Opening issue form on {repo}…")
    if not _open_url(url):
        print("Could not auto-open browser. Open this URL manually:\n")
        print(url)
    return 0
