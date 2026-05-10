import argparse
import ast
import json
import logging
import shutil
import sys
from pathlib import Path
from typing import List, Tuple

try:
    from cellsmith import telemetry
except ImportError:
    import telemetry  # fallback for direct script use

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

class CellAnnotator(ast.NodeVisitor):
    def __init__(self):
        self.insertions: List[Tuple[int, str]] = []
        self.current_class: str = ""
        self.imports_marked: bool = False

    def _handle_import(self, node: ast.AST) -> None:
        if not self.imports_marked and getattr(node, 'col_offset', -1) == 0:
            self.insertions.append((node.lineno, "\n# %% [imports]\n"))
            self.imports_marked = True
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        self._handle_import(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self._handle_import(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        marker = f"# %% [class:{node.name}]\n"
        self.insertions.append((node.lineno, marker))
        previous_class = self.current_class
        self.current_class = node.name
        self.generic_visit(node)
        self.current_class = previous_class

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if self.current_class:
            marker = f"# %% [method:{self.current_class}.{node.name}]\n"
        else:
            marker = f"# %% [func:{node.name}]\n"
        self.insertions.append((node.lineno, marker))
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

def annotate_file(filepath: Path) -> None:
    if not filepath.exists():
        logging.error(f"File not found: {filepath}")
        return

    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()
        
    source = "".join(lines)

    # 1. Identify custom 'knobs' ranges to protect them from inner annotation
    knob_ranges = []
    in_knob = False
    start_line = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("# %% [knobs:") and stripped.endswith(":start]"):
            in_knob = True
            start_line = i + 1  
        elif in_knob and stripped.startswith("# %% [knobs:") and stripped.endswith(":end]"):
            knob_ranges.append((start_line, i + 1))
            in_knob = False

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        logging.error(f"Syntax error in {filepath}: {e}")
        return

    annotator = CellAnnotator()
    annotator.visit(tree)

    # 2. Filter out nodes inside a protected knob block
    valid_insertions = []
    for lineno, marker in annotator.insertions:
        inside_knob = any(start <= lineno <= end for start, end in knob_ranges)
        if not inside_knob:
            valid_insertions.append((lineno, marker))

    # Process bottom-to-top to prevent line shifting issues
    valid_insertions.sort(key=lambda x: x[0], reverse=True)

    # Insert valid markers strictly if they don't already exist
    insert_count = 0
    for lineno, marker in valid_insertions:
        idx = lineno - 1
        already_annotated = False
        
        # Look strictly upward from the target index
        check_idx = idx - 1
        while check_idx >= 0:
            stripped = lines[check_idx].strip()
            if not stripped:  # Skip empty lines to find the true preceding line
                check_idx -= 1
                continue
            if stripped == marker.strip():
                already_annotated = True
            break  # Stop checking at the first non-empty line
            
        if not already_annotated:
            lines.insert(idx, marker)
            insert_count += 1

    # 3. Prepend AI instructions block if it doesn't already exist
    schema_header = (
        "# %% [ai_schema:instructions]\n"
        "# AI INSTRUCTIONS - PATCH SCHEMA:\n"
        "#\n"
        "# To modify this file, return a JSON response with the following structure.\n"
        "# When using CELL_PATCH, `cell_id` MUST be an exact marker that exists in the file.\n"
        "#\n"
        "# {\n"
        "#   \"revisions\": [\n"
        "#     {\n"
        "#       \"filename\": \"path/to/this/file.py\",\n"
        "#       \"revision_type\": \"CELL_PATCH\",  # Or \"REPLACE\", \"CELL_CREATE\"\n"
        "#       \"cell_id\": \"func:my_function\",  # Match an exact marker (e.g. 'imports', 'func:x', 'method:Cls.x')\n"
        "#       \"code_content\": \"# %% [func:my_function]\\ndef my_function():\\n    pass\\n\"\n"
        "#     }\n"
        "#   ]\n"
        "# }\n"
        "#\n"
        "# Choose the most efficient tool for the job (the user pays per token):\n"
        "#   * REPLACE     : For new files, total rewrites, or files under 50 lines.\n"
        "#   * CELL_PATCH  : For surgical updates to a specific function/class/method.\n"
        "#                   `cell_id` MUST exist in the current SKELETON of the file.\n"
        "#   * CELL_CREATE : To append new logic. Use `insert_after` to place it.\n"
        "# %% [ai_schema:end]\n"
        "\n"
    )
    
    if not any("AI INSTRUCTIONS - PATCH SCHEMA" in line for line in lines[:20]):
        lines.insert(0, schema_header)
        insert_count += 1

    if insert_count == 0:
        logging.info(f"No new structures required annotation in {filepath}")
        return

    with open(filepath, "w", encoding="utf-8") as f:
        f.writelines(lines)
    
    logging.info(f"Annotated {filepath} with {insert_count} new elements.")

def create_backup(filepath: Path) -> None:
    if filepath.exists():
        backup_path = filepath.with_suffix(filepath.suffix + ".bak")
        shutil.copy2(filepath, backup_path)
        logging.info(f"Created backup: {backup_path}")

def apply_revisions(data: dict, target_dir: Path) -> None:
    artifacts = data.get("artifacts", [])
    for artifact in artifacts:
        target_file = target_dir / artifact["filename"]
        target_file.parent.mkdir(parents=True, exist_ok=True)
        with open(target_file, "w", encoding="utf-8") as f:
            f.write(artifact["code_content"])
        logging.info(f"Created artifact: {target_file}")

    revisions = data.get("revisions", [])
    for rev in revisions:
        target_file = target_dir / rev["filename"]
        if not target_file.exists():
            logging.warning(f"Target file missing for revision, skipping: {target_file}")
            continue

        create_backup(target_file)
        rev_type = rev.get("revision_type")
        code = rev.get("code_content", "")

        if rev_type == "REPLACE":
            with open(target_file, "w", encoding="utf-8") as f:
                f.write(code)
            logging.info(f"Replaced entire file: {target_file}")
            telemetry.record(telemetry.score_patch(code, "REPLACE"), file=str(target_file))

        elif rev_type == "CELL_PATCH":
            cell_id = rev.get("cell_id")
            if not cell_id:
                logging.error(f"CELL_PATCH missing cell_id for {target_file}")
                continue

            marker = f"# %% [{cell_id}]"
            with open(target_file, "r", encoding="utf-8") as f:
                lines = f.readlines()

            start_idx = -1
            end_idx = len(lines)

            for i, line in enumerate(lines):
                if line.strip() == marker:
                    start_idx = i
                    break

            if start_idx == -1:
                logging.error(f"Marker {marker} not found in {target_file}")
                continue

            is_start_block = ":start]" in marker
            expected_end_marker = marker.replace(":start]", ":end]") if is_start_block else None

            for i in range(start_idx + 1, len(lines)):
                stripped = lines[i].strip()
                if is_start_block:
                    if stripped == expected_end_marker:
                        end_idx = i + 1 
                        break
                else:
                    if stripped.startswith("# %% ["):
                        end_idx = i
                        break

            if not code.endswith("\n"):
                code += "\n"

            new_lines = lines[:start_idx] + [code] + lines[end_idx:]
            
            with open(target_file, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
            
            logging.info(f"Patched cell {cell_id} in {target_file}")
            telemetry.record(telemetry.score_patch(code, "CELL_PATCH"), file=str(target_file), cell_id=cell_id)

        elif rev_type == "CELL_CREATE":
            with open(target_file, "a", encoding="utf-8") as f:
                f.write("\n" + code + "\n")
            logging.info(f"Appended new cell to {target_file}")
            telemetry.record(telemetry.score_patch(code, "CELL_CREATE"), file=str(target_file), cell_id=rev.get("cell_id"))

def strip_file(
    filepath: Path,
    *,
    strip_prompt: bool = True,
    strip_markers: bool = True,
) -> int:
    """Remove the AI schema header and/or `# %% [...]` cell markers from a file.

    Returns the number of lines removed. No-ops on missing file.
    """
    if not filepath.exists():
        logging.warning(f"strip: file not found: {filepath}")
        return 0
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    out: List[str] = []
    skipping_schema = False
    schema_just_ended = False
    for line in lines:
        stripped = line.strip()

        if strip_prompt:
            if stripped == "# %% [ai_schema:instructions]":
                skipping_schema = True
                continue
            if skipping_schema:
                if stripped == "# %% [ai_schema:end]":
                    skipping_schema = False
                    schema_just_ended = True
                continue
            # Drop the trailing instruction comment block that follows ai_schema:end
            # (it's prepended in annotate but not enclosed by markers).
            if schema_just_ended:
                if stripped == "" or stripped.startswith("#"):
                    continue
                schema_just_ended = False

        if strip_markers and stripped.startswith("# %% [") and stripped.endswith("]"):
            continue

        out.append(line)

    removed = len(lines) - len(out)
    if removed:
        with open(filepath, "w", encoding="utf-8") as f:
            f.writelines(out)
    return removed


def _load_gitignore(root: Path):
    """Return a pathspec.PathSpec built from <root>/.gitignore, or None."""
    gi = root / ".gitignore"
    if not gi.exists():
        return None
    try:
        import pathspec
    except ImportError:
        logging.warning("pathspec not installed; skipping .gitignore filtering")
        return None
    with open(gi, "r", encoding="utf-8") as f:
        return pathspec.PathSpec.from_lines("gitwildmatch", f.readlines())


def iter_python_files(
    target: Path,
    *,
    use_gitignore: bool = True,
    include_hidden: bool = False,
) -> List[Path]:
    """Walk `target`, yielding Python files to annotate.

    Skips dotted dirs/files (.git, .venv, ...) and dunder dirs (__pycache__, ...)
    by default, and honors the nearest .gitignore at `target` if present.
    """
    if target.is_file():
        return [target] if target.suffix == ".py" else []

    spec = _load_gitignore(target) if use_gitignore else None
    results: List[Path] = []

    for path in target.rglob("*"):
        if path.is_dir():
            continue
        rel = path.relative_to(target)
        parts = rel.parts

        if not include_hidden and any(p.startswith(".") for p in parts):
            continue
        if any(p.startswith("__") and p.endswith("__") for p in parts[:-1]):
            continue
        if path.suffix != ".py":
            continue
        if spec is not None and spec.match_file(rel.as_posix()):
            continue

        results.append(path)
    return sorted(results)


def rollback_revisions(data: dict, target_dir: Path) -> None:
    """Reverts changes applied by a JSON patch."""
    # 1. Rollback artifacts (delete newly created files)
    artifacts = data.get("artifacts", [])
    for artifact in artifacts:
        target_file = target_dir / artifact["filename"]
        if target_file.exists():
            target_file.unlink()
            logging.info(f"Removed created artifact (rollback): {target_file}")

    # 2. Rollback revisions (restore from .bak)
    revisions = data.get("revisions", [])
    for rev in revisions:
        target_file = target_dir / rev["filename"]
        backup_path = target_file.with_suffix(target_file.suffix + ".bak")
        
        if backup_path.exists():
            shutil.copy2(backup_path, target_file)
            backup_path.unlink()  # Clean up the backup file after restoring
            logging.info(f"Restored file from backup (rollback): {target_file}")
        else:
            logging.warning(f"Backup file not found, cannot rollback: {target_file}")

def main() -> None:
    parser = argparse.ArgumentParser(description="AST-based Code Annotator and JSON Patcher")
    subparsers = parser.add_subparsers(dest="command", required=True)

    annotate_parser = subparsers.add_parser("annotate", help="Annotate Python file(s) with cell markers")
    annotate_parser.add_argument("target", type=Path, help="Target Python file or directory")
    annotate_parser.add_argument("--no-gitignore", action="store_true", help="Don't filter via .gitignore")
    annotate_parser.add_argument("--include-hidden", action="store_true", help="Include dotted (hidden) dirs/files")
    annotate_parser.add_argument("--dry-run", action="store_true", help="List files that would be annotated, don't write")

    patch_parser = subparsers.add_parser("patch", help="Apply JSON response patch to target directory")
    patch_parser.add_argument("json_file", type=Path, help="JSON response file")
    patch_parser.add_argument("target_dir", type=Path, default=Path("."), nargs="?", help="Root directory for patching")

    submit_parser = subparsers.add_parser("submit", help="Open a pre-filled GitHub issue to submit a leaderboard entry")
    submit_parser.add_argument("payload", type=Path, help="JSON patch payload (the file you fed to `cellsmith patch`)")
    submit_parser.add_argument("--context", type=Path, action="append", help="Path to annotated source the LLM saw (file or dir; repeatable)")
    submit_parser.add_argument("--handle", required=True, help="Display name on the leaderboard")
    submit_parser.add_argument("--model", required=True, help="Model name, e.g. gemma-3-4b")
    submit_parser.add_argument("--engine", required=True, help="Inference engine, e.g. mlx, llama.cpp, anthropic-api")
    submit_parser.add_argument("--category", choices=["tiny", "small", "medium", "frontier", "unknown"],
                               help="Self-declared model size bracket: tiny=<4B, small=<10B, medium=<30B, frontier, unknown")
    submit_parser.add_argument("--notes", help="Free-form prompt-engineering notes (optional)")
    submit_parser.add_argument("--print-only", action="store_true", help="Print the URL instead of opening the browser")

    strip_parser = subparsers.add_parser("strip", help="Remove cell markers and/or the AI schema prompt header")
    strip_parser.add_argument("target", type=Path, help="Target Python file or directory")
    strip_parser.add_argument("--prompt-only", action="store_true", help="Only strip the AI schema prompt header")
    strip_parser.add_argument("--markers-only", action="store_true", help="Only strip the # %% cell markers")
    strip_parser.add_argument("--no-gitignore", action="store_true", help="Don't filter via .gitignore (dir mode)")
    strip_parser.add_argument("--include-hidden", action="store_true", help="Include dotted dirs/files (dir mode)")
    strip_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")

    # New Rollback Parser
    rollback_parser = subparsers.add_parser("rollback", help="Rollback changes applied by a JSON patch")
    rollback_parser.add_argument("json_file", type=Path, help="JSON response file used for patching")
    rollback_parser.add_argument("target_dir", type=Path, default=Path("."), nargs="?", help="Root directory for patching")

    args = parser.parse_args()

    if args.command == "annotate":
        if not args.target.exists():
            logging.error(f"Target does not exist: {args.target}")
            sys.exit(1)
        files = iter_python_files(
            args.target,
            use_gitignore=not args.no_gitignore,
            include_hidden=args.include_hidden,
        )
        if not files:
            logging.warning(f"No Python files found under {args.target}")
            return
        if args.dry_run:
            for f in files:
                print(f)
            logging.info(f"[dry-run] {len(files)} file(s) would be annotated")
            return
        for f in files:
            annotate_file(f)
        logging.info(f"Processed {len(files)} file(s)")
    elif args.command == "submit":
        try:
            from cellsmith.submit import run_submit
        except ImportError:
            from submit import run_submit  # script-mode fallback
        sys.exit(run_submit(args, iter_python_files))
    elif args.command == "strip":
        if not args.target.exists():
            logging.error(f"Target does not exist: {args.target}")
            sys.exit(1)
        files = iter_python_files(
            args.target,
            use_gitignore=not args.no_gitignore,
            include_hidden=args.include_hidden,
        )
        if not files:
            logging.warning(f"No Python files found under {args.target}")
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
            apply_revisions(data, args.target_dir)
        elif args.command == "rollback":
            rollback_revisions(data, args.target_dir)

if __name__ == "__main__":
    main()
