"""Passive scoring for cellsmith patches.

Never expose any of this to LLM prompts. The score is purely for human bragging
rights — if the model knows it's being measured, it games the metric (Goodhart).
"""
import ast
import json
import logging
import os
import platform
import time
from pathlib import Path

TOOL_MULTIPLIER = {
    "CELL_PATCH": 1.5,
    "CELL_CREATE": 1.0,
    "REPLACE": 0.5,
}

TELEMETRY_FILE = Path(os.environ.get("CELLSMITH_TELEMETRY", "patch_telemetry.jsonl"))


def score_patch(code_content: str, tool: str) -> dict:
    loc = len(code_content.splitlines())
    try:
        nodes = len(list(ast.walk(ast.parse(code_content))))
    except SyntaxError:
        nodes = 0
    mult = TOOL_MULTIPLIER.get(tool, 1.0)
    return {
        "loc": loc,
        "nodes": nodes,
        "tool": tool,
        "multiplier": mult,
        "score": round(nodes * mult, 2),
    }


def record(entry: dict, *, file: str, cell_id: str | None = None) -> None:
    payload = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "file": file,
        "cell_id": cell_id,
        "platform": platform.system(),
        "model": os.environ.get("CELLSMITH_MODEL", "unknown"),
        "engine": os.environ.get("CELLSMITH_ENGINE", "unknown"),
        **entry,
    }
    try:
        with open(TELEMETRY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")
    except OSError as e:
        logging.warning(f"telemetry write failed: {e}")
