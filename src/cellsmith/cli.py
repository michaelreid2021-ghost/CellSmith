# filepath: src/cellsmith/cli.py
# %% [ai_schema:pointer]
# CellSmith workflow DAG node. Cells marked with `# %% [<cell_id>]`.
# To modify or splice: load `CELLSMITH_PATCH_SCHEMA.md` at the project root
# for the full JSON patch schema (incl. SPLICE_NODE and changelog rules).
# Run `cellsmith status` first — if it errors, edit files directly.
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

# %% [module:adapter_imports:start]
from cellsmith.registry import load_all_adapters
# %% [module:adapter_imports:end]

from cellsmith.adapters.logic_app import (
    unpack_playbook,
    pack_playbook,
)
# %% [imports:end]

# %% [module:init:2:start]
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
# %% [module:init:2:end]


# %% [func:list_adaptors:start]
def _list_adaptors() -> None:
    """Print a formatted table of discovered adapters."""

    adapters = load_all_adapters()
    if not adapters:
        print("No adaptors found.")
        return

    # Find column widths
    name_width = max(len(a.name) for a in adapters)
    version_width = max(len(a.version) for a in adapters)
    desc_width = max(len(a.description) for a in adapters)

    # Header
    hdr = f"{'Name':<{name_width}}  {'Version':<{version_width}}  {'Target Format'}\n"
    hdr += "-" * (name_width + version_width + 3) + "  " + "=" * desc_width + "\n"
    print(hdr)

    # Rows
    for a in adapters:
        print(
            f"{a.name:<{name_width}}  {a.version:<{version_width}}  {a.description}"
        )


# %% [func:main:start]
def main() -> None:
    parser = argparse.ArgumentParser(
        description='AST-based Code Annotator and JSON Patcher'
    )
    subparsers = parser.add_subparsers(dest='command', required=True)

    # --- annotate / annotate-agent / annotate-node ---
    annotate_parser = subparsers.add_parser(
        'annotate', help='Annotate Python/YAML file(s) with cell markers + full schema header'
    )
    annotate_parser.add_argument(
        'target', type=Path, help='Target Python/YAML file or directory'
    )
    annotate_parser.add_argument(
        '--no-gitignore', action='store_true'
    )
    annotate_parser.add_argument(
        '--include-hidden', action='store_true'
    )
    annotate_parser.add_argument(
        '--dry-run', action='store_true'
    )

    agent_parser = subparsers.add_parser(
        'annotate-agent',
        help=f'Like annotate, but uses a laconic pointer header and writes {SKILL_DOC_FILENAME} at the project root',
    )
    agent_parser.add_argument(
        'target', type=Path, help='Target Python/YAML file or directory'
    )
    agent_parser.add_argument(
        '--no-gitignore', action='store_true'
    )
    agent_parser.add_argument(
        '--include-hidden', action='store_true'
    )
    agent_parser.add_argument(
        '--dry-run', action='store_true'
    )
    agent_parser.add_argument(
        '--skill-root', type=Path, default=None,
        help=f'Where to write {SKILL_DOC_FILENAME} (default: target if dir, else target\'s parent)',
    )

    node_parser = subparsers.add_parser(
        'annotate-node',
        help=f'Annotate workflow YAML files with node headers and write DAG-specific {SKILL_DOC_FILENAME} at the project root',
    )
    node_parser.add_argument(
        'target', type=Path, help='Target workflow YAML directory'
    )
    node_parser.add_argument(
        '--no-gitignore', action='store_true'
    )
    node_parser.add_argument(
        '--include-hidden', action='store_true'
    )
    node_parser.add_argument(
        '--dry-run', action='store_true'
    )
    node_parser.add_argument(
        '--skill-root', type=Path, default=None,
        help=f'Where to write {SKILL_DOC_FILENAME} (default: target if dir, else target\'s parent)',
    )

    # --- read ---
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
    read_parser.add_argument(
        '--entry', help='Focal cell, e.g. app.py:func:process:start'
    )
    read_parser.add_argument(
        '--trace-depth', type=int, default=1, help='Call-graph hops rendered in full'
    )
    read_parser.add_argument(
        '--trace-type', default='linear', choices=sorted(TRACE_LEVELS),
        help='How wide a call site may be to be followed',
    )
    read_parser.add_argument(
        '--ast', type=int, default=1, help='Layers beyond the trace rendered as skeletons'
    )
    read_parser.add_argument(
        '--laconic-background', type=int, default=0, help='Layers beyond that rendered as one-liners'
    )
    read_parser.add_argument(
        '--max-characters', type=int, default=DEFAULT_MAX_CHARACTERS, help='Budget, whitespace excluded'
    )
    read_parser.add_argument(
        '--trace-exclude-paths', nargs='*', default=[], help='Cell ids to prune entirely'
    )
    read_parser.add_argument(
        '--trace-keep', nargs='*', default=[], help='Cell ids pinned to full fidelity'
    )
    read_parser.add_argument(
        '--include-files', nargs='*', default=[], help='Extra files to append verbatim'
    )
    read_parser.add_argument(
        '--no-gitignore', action='store_true'
    )
    read_parser.add_argument(
        '--include-hidden', action='store_true'
    )
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

    # --- telemetry / finalize ---
    telemetry_parser = subparsers.add_parser(
        'telemetry',
        help=f'Install the ephemeral telemetry runtime under {AGENTS_DIR}/',
    )
    telemetry_parser.add_argument(
        'target', type=Path, default=Path('.'), nargs='?', help='Project root'
    )
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
    finalize_parser.add_argument(
        'target', type=Path, help='Target Python file or directory'
    )
    finalize_parser.add_argument(
        '--no-gitignore', action='store_true'
    )
    finalize_parser.add_argument(
        '--include-hidden', action='store_true'
    )

    # --- status ---
    subparsers.add_parser(
        'status',
        help='Report whether cellsmith is installed and runnable (for agent probes)',
    )

    # --- adaptors ---
    adaptors_parser = subparsers.add_parser(
        'adaptors',
        help='Discover and list installed CellSmith workflow adaptors',
    )

    # --- patch / rollback ---
    patch_parser = subparsers.add_parser(
        'patch', help='Apply JSON response patch to target directory'
    )
    patch_parser.add_argument(
        'json_file', type=Path, help='JSON response file'
    )
    patch_parser.add_argument(
        'target_dir', type=Path, default=Path('.'), nargs='?', help='Root directory for patching'
    )
    patch_parser.add_argument(
        '--trace', action='store_true',
        help='Wrap the patched cells in ephemeral @focal_trace telemetry',
    )

    strip_parser = subparsers.add_parser(
        'strip', help='Remove cell markers and/or the AI schema prompt header'
    )
    strip_parser.add_argument(
        'target', type=Path, help='Target Python/YAML file or directory'
    )
    strip_parser.add_argument(
        '--prompt-only', action='store_true', help='Only strip the AI schema prompt header'
    )
    strip_parser.add_argument(
        '--markers-only', action='store_true', help='Only strip the # %% cell markers'
    )
    strip_parser.add_argument(
        '--no-gitignore', action='store_true'
    )
    strip_parser.add_argument(
        '--include-hidden', action='store_true'
    )
    strip_parser.add_argument(
        '-y', '--yes', action='store_true', help='Skip confirmation prompt'
    )

    rollback_parser = subparsers.add_parser(
        'rollback', help='Rollback changes applied by a JSON patch'
    )
    rollback_parser.add_argument(
        'json_file', type=Path, help='JSON response file used for patching'
    )
    rollback_parser.add_argument(
        'target_dir', type=Path, default=Path('.'), nargs='?', help='Root directory for patching'
    )

    # --- logic_app (legacy CLI) ---
    la_parser = subparsers.add_parser(
        'logic-app', help='Logic App JSON ↔ Numbered YAML DAG Converter (legacy CLI)'
    )
    la_sub = la_parser.add_subparsers(dest='la_command', required=True)

    la_unpack = la_sub.add_parser(
        'unpack', help='Deconstruct Logic App JSON actions into numbered YAML files'
    )
    la_unpack.add_argument('input_json', type=Path, help='Path to Logic App JSON')
    la_unpack.add_argument('output_dir', type=Path, help='Destination workflow directory')
    la_unpack.add_argument('--intern', action='store_true', help='Compress names to short tokens')

    la_pack = la_sub.add_parser(
        'pack', help='Assemble numbered YAML actions into Logic App JSON'
    )
    la_pack.add_argument('input_dir', type=Path, help='Source workflow directory')
    la_pack.add_argument('output_json', type=Path, help='Destination JSON file')

    args = parser.parse_args()

    # --- adaptors subcommand ---
    if args.command == 'adaptors':
        _list_adaptors()
        return

    # --- status ---
    if args.command == 'status':
        print(f'available cellsmith {__version__}')
        return

    # --- annotate / annotate-agent / annotate-node ---
    if args.command in ('annotate', 'annotate-agent', 'annotate-node'):
        if not args.target.exists():
            logging.error(f'Target does not exist: {args.target}')
            sys.exit(1)

        files = iter_target_files(
            args.target,
            use_gitignore=not getattr(args, 'no_gitignore', False),
            include_hidden=getattr(args, 'include_hidden', False),
        )
        if not files:
            logging.warning(f'No Python/YAML files found under {args.target}')
            return

        if getattr(args, 'dry_run', False):
            for f in files:
                print(f)
            logging.info(
                f'[dry-run] {len(files)} file(s) would be annotated'
            )
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
            skill_root = getattr(args, 'skill_root', None)
            if skill_root is None:
                skill_root = args.target if args.target.is_dir() else args.target.parent

            if args.command == 'annotate-node':
                written = write_node_skill_doc(skill_root)
            else:
                written = write_skill_doc(skill_root)

            logging.info(f'Wrote skill doc to {written}')

        logging.info(f'Processed {len(files)} file(s)')

    # --- telemetry / finalize ---
    elif args.command == 'telemetry':
        if not args.target.exists():
            logging.error(f'Target does not exist: {args.target}')
            sys.exit(1)

        written = ensure_runtime(args.target)
        logging.info(f'Telemetry runtime installed at {written}')
        logging.info(
            f'Traces will be written to {log_path(args.target)}'
        )

        if getattr(args, 'instrument', None) is not None:
            wrapped = instrument_file(
                args.instrument,
                getattr(args, 'cells', None),
            )
            logging.info(
                f'Instrumented {wrapped} cell(s) in {args.instrument}'
            )

    elif args.command == 'finalize':
        if not args.target.exists():
            logging.error(f'Target does not exist: {args.target}')
            sys.exit(1)

        files, lines = finalize_tree(
            args.target,
            use_gitignore=not getattr(args, 'no_gitignore', False),
            include_hidden=getattr(args, 'include_hidden', False),
        )
        logging.info(
            f'Removed {lines} telemetry line(s) from {files} file(s)'
        )

    # --- read ---
    elif args.command == 'read':
        if getattr(args, 'json_file', None) is not None and args.json_file.is_dir():
            args.target_dir = args.json_file
            args.json_file = None

        if not args.target_dir.exists():
            logging.error(f'Target does not exist: {args.target_dir}')
            sys.exit(1)

        discovery_flags = [
            getattr(args, 'list_start_cell', False),
            getattr(args, 'tree', False),
            bool(getattr(args, 'get_cell_list', None)),
            bool(getattr(args, 'get_file_contents', None)),
        ]

        if sum(discovery_flags) > 1:
            logging.error(
                'read: --list-start-cell, --tree, --get-cell-list and '
                '--get-file-contents are mutually exclusive'
            )
            sys.exit(2)

        if any(discovery_flags):
            try:
                if getattr(args, 'list_start_cell', False):
                    print(start_cell_report(args.target_dir))
                elif getattr(args, 'tree', False):
                    print(tree_report(args.target_dir))
                elif getattr(args, 'get_cell_list', None):
                    print(
                        cell_list_report(args.target_dir, args.get_cell_list)
                    )
                else:
                    print(
                        file_contents_report(args.target_dir, args.get_file_contents)
                    )
            except ValueError as e:
                logging.error(f'read failed: {e}')
                sys.exit(1)
            return

        try:
            if args.json_file is not None:
                request = ReadRequest.from_file(args.json_file)
            elif getattr(args, 'entry', None):
                request = request_from_args(args)
            else:
                logging.error(
                    'read requires either a JSON request file or --entry'
                )
                sys.exit(1)
        except ValueError as e:
            logging.error(f'read request rejected: {e}')
            sys.exit(2)

        graph = build_graph(
            args.target_dir,
            use_gitignore=not getattr(args, 'no_gitignore', False),
            include_hidden=getattr(args, 'include_hidden', False),
        )

        try:
            print(compile_read(graph, request))
        except KeyError as e:
            logging.error(f'read failed: {e}')
            sys.exit(5)

    # --- reannotate / strip ---
    elif args.command == 'reannotate':
        if not args.target.exists():
            logging.error(f'Target does not exist: {args.target}')
            sys.exit(1)

        files = iter_target_files(
            args.target,
            use_gitignore=not getattr(args, 'no_gitignore', False),
            include_hidden=getattr(args, 'include_hidden', False),
        )

        if not files:
            logging.warning(
                f'No Python/YAML files found under {args.target}'
            )
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
            use_gitignore=not getattr(args, 'no_gitignore', False),
            include_hidden=getattr(args, 'include_hidden', False),
        )

        if not files:
            logging.warning(
                f'No Python/YAML files found under {args.target}'
            )
            return

        strip_prompt = not getattr(args, 'markers_only', False)
        strip_markers = not getattr(args, 'prompt_only', False)

        parts = []
        if strip_prompt:
            parts.append('AI schema prompt header')
        if strip_markers:
            parts.append('# %% cell markers')

        scope = ', '.join(parts) if parts else '(nothing)'

        if not getattr(args, 'yes', False):
            print(
                f'About to strip {scope} from {len(files)} file(s) '
                f'under {args.target}.'
            )
            print(
                'This is reversible with `cellsmith annotate` but will modify files in-place.'
            )
            ans = input('Proceed? [y/N] ').strip().lower()
            if ans not in ('y', 'yes'):
                logging.info('Aborted.')
                return

        total = 0
        for f in files:
            total += strip_file(
                f,
                strip_prompt=strip_prompt,
                strip_markers=strip_markers,
            )

        logging.info(f'Stripped {total} line(s) across {len(files)} file(s)')

    # --- patch / rollback ---
    elif args.command in ('patch', 'rollback'):
        if args.command == 'rollback':
            args.json_file = find_patch_file(
                args.json_file, args.target_dir
            )

        if not args.json_file.exists():
            logging.error(f'JSON file not found: {args.json_file}')

            if args.command == 'rollback':
                available = filed_patches(args.target_dir)
                if available:
                    logging.error(
                        f"Filed payloads: {','.join(available)}"
                    )

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

    # --- logic-app (legacy) ---
    elif getattr(args, 'la_command', None):
        if args.la_command == 'unpack':
            stats = unpack_playbook(
                args.input_json, args.output_dir, intern=args.intern
            )

            mode = ' (with interning)' if args.intern else ''

            print(
                f'Successfully unpacked{mode} '
                f'{args.input_json} to {args.output_dir}'
            )

            if args.intern and stats:
                print(
                    '\nInterning Compression Report (non-whitespace characters):'
                )
                print(
                    f"  Original length:   {stats['original_chars']:,}"
                )
                print(
                    f"  Interned length:   {stats['interned_chars']:,}"
                )
                print(
                    f"  Characters saved:  {stats['saved_chars']:,} "
                    f"({stats['reduction_pct']:.1f}%)"
                )

        elif args.la_command == 'pack':
            pack_playbook(args.input_dir, None, args.output_json)

            print(
                f'Successfully assembled {args.input_dir} to '
                f'{args.output_json}'
            )


# %% [module:main_guard:start]
if __name__ == "__main__":
    main()
# %% [module:main_guard:end]
