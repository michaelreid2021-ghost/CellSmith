# filepath: src/cellsmith/cli.py
# %% [ai_schema:pointer]
# CellSmith-annotated file. Cells marked with `# %% [<cell_id>]`.
# To modify safely: load `CELLSMITH_PATCH_SCHEMA.md` at the project root
# for the full JSON patch schema (incl. required `changelog` block).
# Run `cellsmith status` first — if it errors, ignore these markers and
# edit the file directly per the user's request.
# %% [ai_schema:end]

# %% [module:init:start]
"""Command-line entrypoint: argument parsing and command routing."""
# %% [module:init:end]

# %% [imports:start]
import argparse
import json
import logging
import sys
from pathlib import Path

from cellsmith import __version__
from cellsmith.annotator import annotate_file
from cellsmith.constants import FULL_SCHEMA_HEADER, POINTER_HEADER, SKILL_DOC_FILENAME
from cellsmith.files import iter_target_files, strip_file
from cellsmith.patcher import (
    AmbiguousMarkerError,
    apply_revisions,
    reannotate_file,
    rollback_revisions,
    write_skill_doc,
)
# %% [imports:end]

# %% [module:init:2:start]
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
# %% [module:init:2:end]


# %% [func:main:start]
def main() -> None:
    parser = argparse.ArgumentParser(description="AST-based Code Annotator and JSON Patcher")
    subparsers = parser.add_subparsers(dest="command", required=True)

    annotate_parser = subparsers.add_parser("annotate", help="Annotate Python/YAML file(s) with cell markers + full schema header")
    annotate_parser.add_argument("target", type=Path, help="Target Python/YAML file or directory")
    annotate_parser.add_argument("--no-gitignore", action="store_true", help="Don't filter via .gitignore")
    annotate_parser.add_argument("--include-hidden", action="store_true", help="Include dotted (hidden) dirs/files")
    annotate_parser.add_argument("--dry-run", action="store_true", help="List files that would be annotated, don't write")

    agent_parser = subparsers.add_parser(
        "annotate-agent",
        help=f"Like annotate, but uses a laconic pointer header and writes {SKILL_DOC_FILENAME} at the project root",
    )
    agent_parser.add_argument("target", type=Path, help="Target Python/YAML file or directory")
    agent_parser.add_argument("--no-gitignore", action="store_true", help="Don't filter via .gitignore")
    agent_parser.add_argument("--include-hidden", action="store_true", help="Include dotted (hidden) dirs/files")
    agent_parser.add_argument("--dry-run", action="store_true", help="List files that would be annotated, don't write")
    agent_parser.add_argument(
        "--skill-root", type=Path, default=None,
        help=f"Where to write {SKILL_DOC_FILENAME} (default: target if dir, else target's parent)",
    )

    reannotate_parser = subparsers.add_parser(
        "reannotate",
        help="Regenerate cell markers from the AST, preserving each file's header variant",
    )
    reannotate_parser.add_argument("target", type=Path, help="Target Python/YAML file or directory")
    reannotate_parser.add_argument("--no-gitignore", action="store_true", help="Don't filter via .gitignore")
    reannotate_parser.add_argument("--include-hidden", action="store_true", help="Include dotted (hidden) dirs/files")

    subparsers.add_parser("status", help="Report whether cellsmith is installed and runnable (for agent probes)")

    patch_parser = subparsers.add_parser("patch", help="Apply JSON response patch to target directory")
    patch_parser.add_argument("json_file", type=Path, help="JSON response file")
    patch_parser.add_argument("target_dir", type=Path, default=Path("."), nargs="?", help="Root directory for patching")

    strip_parser = subparsers.add_parser("strip", help="Remove cell markers and/or the AI schema prompt header")
    strip_parser.add_argument("target", type=Path, help="Target Python/YAML file or directory")
    strip_parser.add_argument("--prompt-only", action="store_true", help="Only strip the AI schema prompt header")
    strip_parser.add_argument("--markers-only", action="store_true", help="Only strip the # %% cell markers")
    strip_parser.add_argument("--no-gitignore", action="store_true", help="Don't filter via .gitignore (dir mode)")
    strip_parser.add_argument("--include-hidden", action="store_true", help="Include dotted dirs/files (dir mode)")
    strip_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")

    rollback_parser = subparsers.add_parser("rollback", help="Rollback changes applied by a JSON patch")
    rollback_parser.add_argument("json_file", type=Path, help="JSON response file used for patching")
    rollback_parser.add_argument("target_dir", type=Path, default=Path("."), nargs="?", help="Root directory for patching")

    args = parser.parse_args()

    if args.command == "status":
        # Stable, parseable single-line output for agent probes.
        print(f"available cellsmith {__version__}")
        return
    if args.command in ("annotate", "annotate-agent"):
        if not args.target.exists():
            logging.error(f"Target does not exist: {args.target}")
            sys.exit(1)
        files = iter_target_files(
            args.target,
            use_gitignore=not args.no_gitignore,
            include_hidden=args.include_hidden,
        )
        if not files:
            logging.warning(f"No Python/YAML files found under {args.target}")
            return
        if args.dry_run:
            for f in files:
                print(f)
            logging.info(f"[dry-run] {len(files)} file(s) would be annotated")
            return
        header = POINTER_HEADER if args.command == "annotate-agent" else FULL_SCHEMA_HEADER
        for f in files:
            annotate_file(f, header=header)
        if args.command == "annotate-agent":
            skill_root = args.skill_root
            if skill_root is None:
                skill_root = args.target if args.target.is_dir() else args.target.parent
            written = write_skill_doc(skill_root)
            logging.info(f"Wrote skill doc to {written}")
        logging.info(f"Processed {len(files)} file(s)")
    elif args.command == "reannotate":
        if not args.target.exists():
            logging.error(f"Target does not exist: {args.target}")
            sys.exit(1)
        files = iter_target_files(
            args.target,
            use_gitignore=not args.no_gitignore,
            include_hidden=args.include_hidden,
        )
        if not files:
            logging.warning(f"No Python/YAML files found under {args.target}")
            return
        root = args.target if args.target.is_dir() else args.target.parent
        for f in files:
            reannotate_file(f, root)
        logging.info(f"Reannotated {len(files)} file(s)")
    elif args.command == "strip":
        if not args.target.exists():
            logging.error(f"Target does not exist: {args.target}")
            sys.exit(1)
        files = iter_target_files(
            args.target,
            use_gitignore=not args.no_gitignore,
            include_hidden=args.include_hidden,
        )
        if not files:
            logging.warning(f"No Python/YAML files found under {args.target}")
            return
        strip_prompt = not args.markers_only
        strip_markers = not args.prompt_only
        what = []
        if strip_prompt:
            what.append("AI schema prompt header")
        if strip_markers:
            what.append("# %% cell markers")
        scope = ", ".join(what) if what else "(nothing)"
        if not args.yes:
            print(f"About to strip {scope} from {len(files)} file(s) under {args.target}.")
            print("This is reversible with `cellsmith annotate` but will modify files in-place.")
            ans = input("Proceed? [y/N] ").strip().lower()
            if ans not in ("y", "yes"):
                logging.info("Aborted.")
                return
        total = 0
        for f in files:
            total += strip_file(f, strip_prompt=strip_prompt, strip_markers=strip_markers)
        logging.info(f"Stripped {total} line(s) across {len(files)} file(s)")
    elif args.command in ["patch", "rollback"]:
        if not args.json_file.exists():
            logging.error(f"JSON file not found: {args.json_file}")
            sys.exit(1)

        with open(args.json_file, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError as e:
                logging.error(f"Invalid JSON: {e}")
                sys.exit(1)

        if args.command == "patch":
            try:
                all_ok = apply_revisions(data, args.target_dir)
            except AmbiguousMarkerError as e:
                print(e.report)
                sys.exit(4)
            except ValueError as e:
                logging.error(f"patch rejected: {e}")
                sys.exit(2)
            if not all_ok:
                sys.exit(3)
        elif args.command == "rollback":
            rollback_revisions(data, args.target_dir)
# %% [func:main:end]

# %% [module:main_guard:start]
if __name__ == "__main__":
    main()
# %% [module:main_guard:end]
