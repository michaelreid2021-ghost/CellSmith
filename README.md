# **CellSmith**

Schema-driven code patching for LLM workflows.  
CellSmith annotates Python source with stable AST-derived cell markers so language models can return compact JSON patches targeting individual functions, methods, classes, or module sections instead of regenerating entire files.  
It parses the AST, injects non-destructive Jupyter-style markers, validates patches, manages versioned backups, and supports one-command rollback.

## **Features**

* **AST-aware annotation** — Automatically places markers around imports, functions, classes, methods, and module sections (\# %% \[func:name:start\], \# %% \[class:Foo:start\], \# %% \[method:Cls.x:start\], protected \# %% \[knobs:\*\] zones). Nested functions inherit their parent cell.  
* **Surgical JSON patching** — Supports CELL\_PATCH, REPLACE, CELL\_CREATE, FILE\_CREATE, FILE\_MOVE, FILE\_DELETE, and ARCHIVE in a single payload.  
* **Recoverable deletes** — FILE\_DELETE removes a file from the working tree and keeps its content in .cellsmith/archive/, so a delete made mid-refactor can be undone without discarding every change since the last commit.  
* **Token-efficient agent mode** — annotate-agent replaces full schema headers with short pointers and writes a single shared CELLSMITH\_PATCH\_SCHEMA.md at the project root.  
* **Mandatory changelog gate** — Every patch must contain a validated changelog block. Invalid or missing entries are rejected before any disk writes.  
* **Dynamic resolution context** — cellsmith read renders a call-graph slice at three fidelities: full code on the execution trace, signature-and-docstring skeletons beyond it, one-line summaries further out. Bounded by a character budget applied at cell boundaries. Four discovery flags (--list-start-cell, \--tree, \--get-cell-list, \--get-file-contents) answer orientation questions so agents never reach for cat, ls or find.  
* **Unambiguous targeting** — A cell\_id must resolve to exactly one marker. Duplicate markers are rejected with exit code 4 before any write. cellsmith reannotate regenerates markers from the AST.  
* **Ephemeral focal telemetry** — patch \--trace wraps the patched cells in a @focal\_trace decorator that writes one JSON record per call to .agents/logs/focal\_session.jsonl. cellsmith finalize removes it again.  
* **Workflow DAG adapters** — Logic App / playbook JSON ↔ numbered YAML trees, optional lexicon interning (`A001`…), `annotate-node`, and surgical `SPLICE_NODE` insertion with dependency rewiring.
* **Safety defaults** — Automatic versioned backups, post-patch syntax validation, and atomic rollback.

PLACEHOLDER_REST_SEE_FILE