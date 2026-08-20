# filepath: src/cellsmith/reader/schema.py
# %% [ai_schema:pointer]
# CellSmith-annotated file. Cells marked with `# %% [<cell_id>]`.
# To modify safely: load `CELLSMITH_PATCH_SCHEMA.md` at the project root
# for the full JSON patch schema (incl. required `changelog` block).
# Run `cellsmith status` first — if it errors, ignore these markers and
# edit the file directly per the user's request.
# %% [ai_schema:end]

# %% [module:init:start]
"""Validation for the `CellRead` request payload.

Agents emit JSON rather than shell flags, so this is the primary interface.
Every field except `entry` is optional; unknown fields are rejected rather
than ignored, so a typo surfaces immediately instead of silently changing
what the agent sees.
"""
# %% [module:init:end]

# %% [imports:start]
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from cellsmith.reader.graph import TRACE_LEVELS
# %% [imports:end]

# %% [module:init:2:start]
DEFAULT_MAX_CHARACTERS = 50000
GRACE_BUFFER = 500

_FIELDS = {
    "entry",
    "trace_depth",
    "trace_type",
    "ast_layers",
    "laconic_background_layers",
    "max_characters",
    "trace_exclude_paths",
    "trace_keep",
    "include_files",
}
# %% [module:init:2:end]


@dataclass
# %% [class:ReadRequest:start]
class ReadRequest:
    """A resolved, validated read request."""

    entry: str
    trace_depth: int = 1
    trace_type: str = "linear"
    ast_layers: int = 1
    laconic_background_layers: int = 0
    max_characters: int = DEFAULT_MAX_CHARACTERS
    trace_exclude_paths: List[str] = field(default_factory=list)
    trace_keep: List[str] = field(default_factory=list)
    include_files: List[str] = field(default_factory=list)

    @classmethod
# %% [method:ReadRequest.from_dict:start]
    def from_dict(cls, data: Dict[str, Any]) -> "ReadRequest":
        """Build a request from a payload, raising ValueError on any problem."""
        if not isinstance(data, dict):
            raise ValueError("read request must be a JSON object")

        # Accept both the wrapped and bare forms.
        payload = data.get("read_request", data)
        if not isinstance(payload, dict):
            raise ValueError("`read_request` must be a JSON object")

        unknown = sorted(set(payload) - _FIELDS)
        if unknown:
            raise ValueError(
                f"unknown field(s) {unknown} in read request; "
                f"valid fields are {sorted(_FIELDS)}"
            )

        entry = payload.get("entry")
        if not isinstance(entry, str) or not entry.strip():
            raise ValueError("read request requires a non-empty `entry` string")

        def _int(name: str, default: int, minimum: int = 0) -> int:
            value = payload.get(name, default)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"`{name}` must be an integer; got {value!r}")
            if value < minimum:
                raise ValueError(f"`{name}` must be >= {minimum}; got {value}")
            return value

        def _str_list(name: str) -> List[str]:
            value = payload.get(name, [])
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ValueError(f"`{name}` must be an array of strings")
            return list(value)

        trace_type = payload.get("trace_type", "linear")
        if trace_type not in TRACE_LEVELS:
            raise ValueError(
                f"`trace_type` must be one of {sorted(TRACE_LEVELS)}; got {trace_type!r}"
            )

        return cls(
            entry=entry.strip(),
            trace_depth=_int("trace_depth", 1),
            trace_type=trace_type,
            ast_layers=_int("ast_layers", 1),
            laconic_background_layers=_int("laconic_background_layers", 0),
            max_characters=_int("max_characters", DEFAULT_MAX_CHARACTERS, minimum=1),
            trace_exclude_paths=_str_list("trace_exclude_paths"),
            trace_keep=_str_list("trace_keep"),
            include_files=_str_list("include_files"),
        )
# %% [method:ReadRequest.from_dict:end]

    @classmethod
# %% [method:ReadRequest.from_file:start]
    def from_file(cls, path: Path) -> "ReadRequest":
        """Load and validate a request from a JSON file."""
        import json

        with open(path, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError as e:
                raise ValueError(f"invalid JSON in {path}: {e}") from e
        return cls.from_dict(data)
# %% [method:ReadRequest.from_file:end]
# %% [class:ReadRequest:end]


# %% [func:request_from_args:start]
def request_from_args(args: Any) -> ReadRequest:
    """Build a request from parsed CLI arguments."""
    return ReadRequest.from_dict({
        "entry": args.entry,
        "trace_depth": args.trace_depth,
        "trace_type": args.trace_type,
        "ast_layers": args.ast,
        "laconic_background_layers": args.laconic_background,
        "max_characters": args.max_characters,
        "trace_exclude_paths": list(args.trace_exclude_paths or []),
        "trace_keep": list(args.trace_keep or []),
        "include_files": list(args.include_files or []),
    })
# %% [func:request_from_args:end]


# %% [module:init:3:start]
__all__ = [
    "ReadRequest",
    "request_from_args",
    "DEFAULT_MAX_CHARACTERS",
    "GRACE_BUFFER",
]
# %% [module:init:3:end]
