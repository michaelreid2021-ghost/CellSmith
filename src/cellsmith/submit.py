"""`cellsmith submit` — open a pre-filled GitHub issue in the browser.

Reads your patch JSON, counts characters in any --context files you point at
(locally, nothing uploaded), then opens a pre-filled GitHub issue form.
Click submit. Done.

What goes in the URL: handle / model / engine / category / payload / input_chars
What never leaves your machine: your source code
"""
from __future__ import annotations

import logging
import os
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

# GitHub rejects GETs over ~8 KB. Keep payload well under that.
MAX_PAYLOAD_BYTES = 6_000


def _count_input_chars(context_paths: Iterable[Path], iter_python_files) -> int:
    """Count total characters across all Python files. Source stays on disk."""
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
    payload: str,
    input_chars: int,
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
        "input_chars": str(input_chars),
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
            import time, os as _os
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
    payload = payload_path.read_text(encoding="utf-8")

    payload_too_large = len(payload.encode("utf-8")) > MAX_PAYLOAD_BYTES
    if payload_too_large:
        logging.warning(
            f"Payload is {len(payload.encode('utf-8')):,} bytes "
            f"(URL cap ~{MAX_PAYLOAD_BYTES:,} bytes). "
            "Form will open without it — paste the JSON manually."
        )
        payload = ""

    input_chars = 0
    if args.context:
        input_chars = _count_input_chars(args.context, iter_python_files)
        print(f"Input context: {input_chars:,} characters.")

    url = build_url(
        repo,
        handle=args.handle,
        model=args.model,
        engine=args.engine,
        payload=payload,
        input_chars=input_chars,
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
    else:
        print("Browser opened. Review the pre-filled form and click Submit.")
        if payload_too_large:
            print(
                "\n⚠  Payload was too large to pre-fill. Paste the contents of:\n"
                f"   {payload_path.resolve()}\n"
                "into the 'JSON patch payload' field before submitting."
            )
    return 0
