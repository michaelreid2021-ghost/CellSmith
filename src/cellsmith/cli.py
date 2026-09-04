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
from cellsmith.adapters.dag import NODE_POINTER_HEADER, write_node_skill_doc
from cellsmith.annotator import annotate_file
from cellsmith.constants import FULL_SCHEMA_HEADER, POINTER_HEADER, SKILL_DOC_FILENAME
from cellsmith.files import iter_target_files, strip_file
from cellsmith.reader import build_graph
from cellsmith.workspace import filed_patches, find_patch_file
from cellsmith.telemetry import (
    AGENTS_DIR,
    ensure_runtime,
    finalize_tree,
    instrument_file,
    log_path,
)
from cellsmith.reader.graph import TRACE_LEVELS
from cellsmith.reader.compiler import compile_read
from cellsmith.survey import (
    cell_list_report,
    file_contents_report,
    start_cell_report,
    tree_report,
)
from cellsmith.reader.schema import (
    DEFAULT_MAX_CHARACTERS,
    ReadRequest,
    request_from_args,
)
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
    parser = argparse.ArgumentParser(description='AST-based Code Annotator and JSON Patcher')
    subparsers = parser.add_subparsers(dest='command', required=True)

    annotate_parser = subparsers.add_parser('annotate', help='Annotate Python/YAML file(s) with cell markers + full schema header')
    annotate_parser.add_argument('target', type=Path, help='Target Python/YAML file or directory')
    annotate_parser.add_argument('--no-gitignore', action='store_true', help='Don\'t filter via .gitignore')
    annotate_parser.add_argument('--include-hidden', action='store_true', help='Include dotted (hidden) dirs/files')
    annotate_parser.add_argument('--dry-run', action='store_true', help='List files that would be annotated, don\'t write')

    agent_parser = subparsers.add_parser(
        'annotate-agent',
        help=f'Like annotate, but uses a laconic pointer header and writes {SKILL_DOC_FILENAME} at the project root',
    )
    agent_parser.add_argument('target', type=Path, help='Target Python/YAML file or directory')
    agent_parser.add_argument('--no-gitignore', action='store_true', help='Don\'t filter via .gitignore')
    agent_parser.add_argument('--include-hidden', action='store_true', help='Include dotted (hidden) dirs/files')
    agent_parser.add_argument('--dry-run', action='store_true', help='List files that would be annotated, don\'t write')
    agent_parser.add_argument(
        '--skill-root', type=Path, default=None,
        help=f'Where to write {SKILL_DOC_FILENAME} (default: target if dir, else target\'s parent)',
    )

    node_parser = subparsers.add_parser(
        'annotate-node',
        help=f'Annotate workflow YAML files with node headers and write DAG-specific {SKILL_DOC_FILENAME} at the project root',
    )
    node_parser.add_argument('target', type=Path, help='Target workflow YAML directory')
    node_parser.add_argument('--no-gitignore', action='store_true', help='Don\'t filter via .gitignore')
    node_parser.add_argument('--include-hidden', action='store_true', help='Include dotted (hidden) dirs/files')
    node_parser.add_argument('--dry-run', action='store_true', help='List files that would be annotated, don\'t write')
    node_parser.add_argument(
        '--skill-root', type=Path, default=None,
        help=f'Where to write {SKILL_DOC_FILENAME} (default: target if dir, else target\'s parent)',
    )

    reannotate_parser = subparsers.add_parser(
        'reannotate',
        help='Regenerate cell markers from the AST, preserving each file\'s header variant',
    )
    reannotate_parser.add_argument('target', type=Path, help='Target Python/YAML file or directory')
    reannotate_parser.add_argument('--no-gitignore', action='store_true', help='Don\'t filter via .gitignore')
    reannotate_parser.add_argument('--include-hidden', action='store_true', help='Include dotted (hidden) dirs/files')

    read_parser = subparsers.add_parser(
        'read',
        help='Compile a dynamic resolution context around an entry cell '
             '(also: --list-start-cell, --tree, --get-cell-list, --get-file-contents)',
    )
    read_parser.add_argument(
        'json_file', type=Path, nargs='?', default=None,
        help='JSON read request (omit to use --entry and the flags below)',
    )
    read_parser.add_argument(
        'target_dir', type=Path, default=Path('.'), nargs='?',
        help='Project root to index',
    )
    read_parser.add_argument('--entry', help='Focal cell, e.g. app.py:func:process:start')
    read_parser.add_argument('--trace-depth', type=int, default=1, help='Call-graph hops rendered in full')
    read_parser.add_argument(
        '--trace-type', default='linear', choices=sorted(TRACE_LEVELS),
        help='How wide a call site may be to be followed',
    )
    read_parser.add_argument('--ast', type=int, default=1, help='Layers beyond the trace rendered as skeletons')
    read_parser.add_argument('--laconic-background', type=int, default=0, help='Layers beyond that rendered as one-liners')
    read_parser.add_argument('--max-characters', type=int, default=DEFAULT_MAX_CHARACTERS, help='Budget, whitespace excluded')
    read_parser.add_argument('--trace-exclude-paths', nargs='*', default=[], help='Cell ids to prune entirely')
    read_parser.add_argument('--trace-keep', nargs='*', default=[], help='Cell ids pinned to full fidelity')
    read_parser.add_argument('--include-files', nargs='*', default=[], help='Extra files to append verbatim')
    read_parser.add_argument('--no-gitignore', action='store_true', help='Don\'t filter via .gitignore')
    read_parser.add_argument('--include-hidden', action='store_true', help='Include dotted (hidden) dirs/files')
    read_parser.add_argument(
        '--list-start-cell', action='store_true',
        help='List probable entry-point cells from manifest files '
             '(pyproject.toml, setup.cfg/setup.py, Dockerfiles); falls back to main.py/app.py',
    )
    read_parser.add_argument(
        '--get-cell-list', metavar='FILE',
        help='List the cells in one supported file (.py, .yaml, .yml)',
    )
    read_parser.add_argument(
        '--get-file-contents', metavar='FILE',
        help='Print the raw contents of one unsupported file '
             '(supported files are served by the cell-aware tools)',
    )
    read_parser.add_argument(
        '--tree', action='store_true',
        help='File tree honoring .gitignore and .ignore; hidden files included',
    )

    telemetry_parser = subparsers.add_parser(
        'telemetry',
        help=f'Install the ephemeral telemetry runtime under {AGENTS_DIR}/',
    )
    telemetry_parser.add_argument('target', type=Path, default=Path('.'), nargs='?', help='Project root')
    telemetry_parser.add_argument(
        '--instrument', type=Path, default=None,
        help='Also wrap every top-level function/method in this file',
    )
    telemetry_parser.add_argument(
        '--cells', nargs='*', default=None,
        help='With --instrument, wrap only these cell ids',
    )

    finalize_parser = subparsers.add_parser(
        'finalize',
        help='Strip all @focal_trace decorators and telemetry imports',
    )
    finalize_parser.add_argument('target', type=Path, help='Target Python file or directory')
    finalize_parser.add_argument('--no-gitignore', action='store_true', help='Don\'t filter via .gitignore')
    finalize_parser.add_argument('--include-hidden', action='store_true', help='Include dotted (hidden) dirs/files')

    subparsers.add_parser('status', help='Report whether cellsmith is installed and runnable (for agent probes)')

    patch_parser = subparsers.add_parser('patch', help='Apply JSON response patch to target directory')
    patch_parser.add_argument('json_file', type=Path, help='JSON response file')
    patch_parser.add_argument('target_dir', type=Path, default=Path('.'), nargs='?', help='Root directory for patching')
    patch_parser.add_argument(
        '--trace', action='store_true',
        help='Wrap the patched cells in ephemeral @focal_trace telemetry',
    )

    strip_parser = subparsers.add_parser('strip', help='Remove cell markers and/or the AI schema prompt header')
    strip_parser.add_argument('target', type=Path, help='Target Python/YAML file or directory')
    strip_parser.add_argument('--prompt-only', action='store_true', help='Only strip the AI schema prompt header')
    strip_parser.add_argument('--markers-only', action='store_true', help='Only strip the # %% cell markers')
    strip_parser.add_argument('--no-gitignore', action='store_true', help='Don\'t filter via .gitignore (dir mode)')
    strip_parser.add_argument('--include-hidden', action='store_true', help='Include dotted dirs/files (dir mode)')
    strip_parser.add_argument('-y', '--yes', action='store_true', help='Skip confirmation prompt')

    rollback_parser = subparsers.add_parser('rollback', help='Rollback changes applied by a JSON patch')
    rollback_parser.add_argument('json_file', type=Path, help='JSON response file used for patching')
    rollback_parser.add_argument('target_dir', type=Path, default=Path('.'), nargs='?', help='Root directory for patching')

    args = parser.parse_args()

    if args.command == 'status':
        print(f'available cellsmith {__version__}')
        return
    if args.command in ('annotate', 'annotate-agent', 'annotate-node'):
        if not args.target.exists():
            logging.error(f'Target does not exist: {args.target}')
            sys.exit(1)
        files = iter_target_files(
            args.target,
            use_gitignore=not args.no_gitignore,
            include_hidden=args.include_hidden,
        )
        if not files:
            logging.warning(f'No Python/YAML files found under {args.target}')
            return
        if args.dry_run:
            for f in files:
                print(f)
            logging.info(f'[dry-run] {len(files)} file(s) would be annotated')
            return

        if args.command == 'annotate-node':
            header = NODE_POINTER_HEADER
        elif args.command == 'annotate-agent':
            header = POINTER_HEADER
        else:
            header = FULL_SCHEMA_HEADER

        for f in files:
            annotate_file(f, header=header)

        if args.command in ('annotate-agent', 'annotate-node'):
            skill_root = args.skill_root
            if skill_root is None:
                skill_root = args.target if args.target.is_dir() else args.target.parent
            if args.command == 'annotate-node':
                written = write_node_skill_doc(skill_root)
            else:
                written = write_skill_doc(skill_root)
            logging.info(f'Wrote skill doc to {written}')
        logging.info(f'Processed {len(files)} file(s)')
    elif args.command == 'telemetry':
        if not args.target.exists():
            logging.error(f'Target does not exist: {args.target}')
            sys.exit(1)
        written = ensure_runtime(args.target)
        logging.info(f'Telemetry runtime installed at {written}')
        logging.info(f'Traces will be written to {log_path(args.target)}')
        if args.instrument is not None:
            wrapped = instrument_file(args.instrument, args.cells)
            logging.info(f'Instrumented {wrapped} cell(s) in {args.instrument}')
    elif args.command == 'finalize':
        if not args.target.exists():
            logging.error(f'Target does not exist: {args.target}')
            sys.exit(1)
        files, lines = finalize_tree(
            args.target,
            use_gitignore=not args.no_gitignore,
            include_hidden=args.include_hidden,
        )
        logging.info(f'Removed {lines} telemetry line(s) from {files} file(s)')
    elif args.command == 'read':
        if args.json_file is not None and args.json_file.is_dir():
            args.target_dir = args.json_file
            args.json_file = None
        if not args.target_dir.exists():
            logging.error(f'Target does not exist: {args.target_dir}')
            sys.exit(1)
        discovery_flags = [
            args.list_start_cell,
            args.tree,
            bool(args.get_cell_list),
            bool(args.get_file_contents),
        ]
        if sum(discovery_flags) > 1:
            logging.error(
                'read: --list-start-cell, --tree, --get-cell-list and '
                '--get-file-contents are mutually exclusive'
            )
            sys.exit(2)
        if any(discovery_flags) and (args.entry or args.json_file):
            logging.error(
                'read: the discovery flags take a target dir only; '
                'drop --entry and the JSON request file'
            )
            sys.exit(2)
        if any(discovery_flags):
            try:
                if args.list_start_cell:
                    print(start_cell_report(args.target_dir))
                elif args.tree:
                    print(tree_report(args.target_dir))
                elif args.get_cell_list:
                    print(cell_list_report(args.target_dir, args.get_cell_list))
                else:
                    print(file_contents_report(args.target_dir, args.get_file_contents))
            except ValueError as e:
                logging.error(f'read failed: {e}')
                sys.exit(1)
            return
        try:
            if args.json_file is not None:
                request = ReadRequest.from_file(args.json_file)
            elif args.entry:
                request = request_from_args(args)
            else:
                logging.error('read requires either a JSON request file or --entry')
                sys.exit(1)
        except ValueError as e:
            logging.error(f'read request rejected: {e}')
            sys.exit(2)

        graph = build_graph(
            args.target_dir,
            use_gitignore=not args.no_gitignore,
            include_hidden=args.include_hidden,
        )
        try:
            print(compile_read(graph, request))
        except KeyError as e:
            logging.error(f'read failed: {e}')
            sys.exit(5)
    elif args.command == 'reannotate':
        if not args.target.exists():
            logging.error(f'Target does not exist: {args.target}')
            sys.exit(1)
        files = iter_target_files(
            args.target,
            use_gitignore=not args.no_gitignore,
            include_hidden=args.include_hidden,
        )
        if not files:
            logging.warning(f'No Python/YAML files found under {args.target}')
            return
        root = args.target if args.target.is_dir() else args.target.parent
        for f in files:
            reannotate_file(f, root)
        logging.info(f'Reannotated {len(files)} file(s)')
    elif args.command == 'strip':
        if not args.target.exists():
            logging.error(f'Target does not exist: {args.target}')
            sys.exit(1)
        files = iter_target_files(
            args.target,
            use_gitignore=not args.no_gitignore,
            include_hidden=args.include_hidden,
        )
        if not files:
            logging.warning(f'No Python/YAML files found under {args.target}')
            return
        strip_prompt = not args.markers_only
        strip_markers = not args.prompt_only
        what = []
        if strip_prompt:
            what.append('AI schema prompt header')
        if strip_markers:
            what.append('# %% cell markers')
        scope = ', '.join(what) if what else '(nothing)'
        if not args.yes:
            print(f'About to strip {scope} from {len(files)} file(s) under {args.target}.')
            print('This is reversible with `cellsmith annotate` but will modify files in-place.')
            ans = input('Proceed? [y/N] ').strip().lower()
            if ans not in ('y', 'yes'):
                logging.info('Aborted.')
                return
        total = 0
        for f in files:
            total += strip_file(f, strip_prompt=strip_prompt, strip_markers=strip_markers)
        logging.info(f'Stripped {total} line(s) across {len(files)} file(s)')
    elif args.command in ['patch', 'rollback']:
        if args.command == 'rollback':
            args.json_file = find_patch_file(args.json_file, args.target_dir)
        if not args.json_file.exists():
            logging.error(f'JSON file not found: {args.json_file}')
            if args.command == 'rollback':
                available = filed_patches(args.target_dir)
                if available:
                    logging.error(f'Filed payloads: {','.join(available)}')
            sys.exit(1)

        with open(args.json_file, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError as e:
                logging.error(f'Invalid JSON: {e}')
                sys.exit(1)

        if args.command == 'patch':
            try:
                all_ok = apply_revisions(
                    data, args.target_dir, trace=args.trace, json_file=args.json_file
                )
            except AmbiguousMarkerError as e:
                print(e.report)
                sys.exit(4)
            except ValueError as e:
                logging.error(f'patch rejected: {e}')
                sys.exit(2)
            if not all_ok:
                sys.exit(3)
        elif args.command == 'rollback':
            rollback_revisions(data, args.target_dir)
# %% [func:main:end]

# %% [module:main_guard:start]
if __name__ == "__main__":
    main()
# %% [module:main_guard:end]
