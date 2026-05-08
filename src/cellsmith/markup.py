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
            self.insertions.append((node.lineno, "# %% [imports]\n"))
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
        "# To modify this file, return a JSON response with the following structure. When CELL_PATCH it must be the exact cell_id that exists in the file you are submitting an update for."
        "# {\n"
        "#   \"revisions\": [\n"
        "#     {\n"
        "#       \"filename\": \"path/to/this/file.py\",\n"
        "#       \"revision_type\": \"CELL_PATCH\",  # Or \"REPLACE\", \"CELL_CREATE\"\n"
        "#       \"cell_id\": \"func:my_function\",  # Match the exact marker ID (e.g., 'imports', 'func:x')\n"
        "#       \"code_content\": \"# %% [func:my_function]\\ndef my_function():\\n    pass\\n\"\n"
        "#     }\n"
        "#   ]\n"
        "# }\n"
        "# %% [ai_schema:end]\n\n"
        "#You must choose the most efficient tool for the job to respect the user's token cost and the Auditor's requirements:"
        "#* **REPLACE**: For new files, total rewrites, or files under 50 lines."
        "#* **CELL_PATCH**: For surgical updates to specific functions or classes. You MUST use a valid `cell_id` (e.g., `func:name`, `class:Name`, `method:Class.name`) that exists in the current **SKELETON**, or #file that is the **FOCUS** of the current ask."
        "#* **CELL_CREATE**: To append new logic, using `insert_after` to specify placement."
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

    annotate_parser = subparsers.add_parser("annotate", help="Annotate a Python file with cell markers")
    annotate_parser.add_argument("target", type=Path, help="Target Python file")

    patch_parser = subparsers.add_parser("patch", help="Apply JSON response patch to target directory")
    patch_parser.add_argument("json_file", type=Path, help="JSON response file")
    patch_parser.add_argument("target_dir", type=Path, default=Path("."), nargs="?", help="Root directory for patching")

    # New Rollback Parser
    rollback_parser = subparsers.add_parser("rollback", help="Rollback changes applied by a JSON patch")
    rollback_parser.add_argument("json_file", type=Path, help="JSON response file used for patching")
    rollback_parser.add_argument("target_dir", type=Path, default=Path("."), nargs="?", help="Root directory for patching")

    args = parser.parse_args()

    if args.command == "annotate":
        annotate_file(args.target)
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