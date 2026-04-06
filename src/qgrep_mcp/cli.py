"""CLI interface for qgrep-mcp — use indexed code search from the terminal.

Usage:
    qgrep-mcp search <pattern> [path] [options]
    qgrep-mcp index build|rebuild|status|delete [path]
    qgrep-mcp estimate [path]
    qgrep-mcp serve [--http [--port PORT]]
"""

import argparse
import asyncio
import json
import os
import sys

from .estimator import CostEstimator
from .search import SearchOrchestrator


def _resolve_path(path: str | None) -> str:
    """Resolve a path argument to an absolute real path, defaulting to cwd."""
    p = path or os.getcwd()
    return os.path.realpath(os.path.expanduser(p))


async def _cmd_search(args: argparse.Namespace) -> int:
    """Execute a code search and print results."""
    path = _resolve_path(args.path)
    estimator = CostEstimator()
    orchestrator = SearchOrchestrator(estimator)

    result = await orchestrator.search(
        args.pattern,
        path,
        glob=args.glob,
        case_insensitive=args.ignore_case,
        output_mode=args.output_mode,
        context_lines=args.context,
        max_results=args.max_results,
    )

    if result.error:
        print(f"error: {result.error}", file=sys.stderr)
        return 1

    for line in result.matches:
        print(line)

    if args.stats:
        print(
            f"\n--- {result.match_count} matches in {result.file_count} files "
            f"[{result.backend}, {result.elapsed_seconds}s]"
            f"{' (truncated)' if result.truncated else ''} ---",
            file=sys.stderr,
        )
    return 0


async def _cmd_index(args: argparse.Namespace) -> int:
    """Manage search indexes."""
    from .index import build_index, delete_index, index_status

    path = _resolve_path(args.path)
    action = args.action

    if action == "status":
        status = await index_status(path)
        if status.get("indexed"):
            print(f"Indexed: {path}")
            print(f"  project:    {status['project_name']}")
            print(f"  built:      {status['build_time_seconds']:.1f}s")
        else:
            print(f"Not indexed: {path}")
        return 0

    if action == "delete":
        deleted = await delete_index(path)
        print("Deleted." if deleted else "No index found.")
        return 0

    if action in ("build", "rebuild"):
        if action == "rebuild":
            await delete_index(path)
        try:
            print(f"Building index for {path} ...")
            meta = await build_index(path)
            print(f"Done in {meta.build_time_seconds:.1f}s")
            return 0
        except RuntimeError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1

    print(f"Unknown action: {action}", file=sys.stderr)
    return 1


async def _cmd_estimate(args: argparse.Namespace) -> int:
    """Analyze a repo and recommend whether to index."""
    from .config import has_qgrep
    from .index import has_index
    from .ripgrep import count_files

    path = _resolve_path(args.path)
    estimator = CostEstimator()

    fc = await count_files(path)
    estimator.record_file_count(path, fc)

    rec = estimator.estimate(path, has_index=has_index(path), has_qgrep=has_qgrep())

    if args.json:
        print(json.dumps({
            "recommendation": rec.action,
            "confidence": rec.confidence,
            "reasoning": rec.reasoning,
            **rec.stats,
        }, indent=2))
    else:
        print(f"Path:           {path}")
        print(f"Files:          {rec.stats['file_count']:,}")
        print(f"Recommendation: {rec.action}")
        print(f"Confidence:     {rec.confidence}")
        print(f"Reason:         {rec.reasoning}")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    """Start the MCP or HTTP server."""
    if args.http:
        from .api import run_http
        run_http(port=args.port)
    else:
        from .server import main as mcp_main
        mcp_main()
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="qgrep-mcp",
        description="Indexed code search — up to 300x faster than grep on large repos.",
    )
    sub = parser.add_subparsers(dest="command")

    # --- search ---
    p_search = sub.add_parser("search", help="Search code in a directory")
    p_search.add_argument("pattern", help="Regex pattern to search for")
    p_search.add_argument("path", nargs="?", default=None, help="Directory to search (default: cwd)")
    p_search.add_argument("-g", "--glob", help="Glob filter (e.g. '*.py')")
    p_search.add_argument("-i", "--ignore-case", action="store_true", help="Case-insensitive search")
    p_search.add_argument("-m", "--max-results", type=int, default=200, help="Max results (default: 200)")
    p_search.add_argument("-C", "--context", type=int, default=0, help="Context lines around matches")
    p_search.add_argument(
        "-o", "--output-mode",
        choices=["content", "files_with_matches", "count"],
        default="content",
        help="Output mode (default: content)",
    )
    p_search.add_argument("-l", dest="output_mode", action="store_const", const="files_with_matches",
                          help="List matching files only (short for -o files_with_matches)")
    p_search.add_argument("-c", dest="output_mode_count", action="store_true",
                          help="Show match counts per file (short for -o count)")
    p_search.add_argument("--stats", action="store_true", help="Print search stats to stderr")

    # --- index ---
    p_index = sub.add_parser("index", help="Manage search indexes")
    p_index.add_argument("action", choices=["build", "rebuild", "status", "delete"],
                         help="Index action")
    p_index.add_argument("path", nargs="?", default=None, help="Directory (default: cwd)")

    # --- estimate ---
    p_estimate = sub.add_parser("estimate", help="Check if a repo would benefit from indexing")
    p_estimate.add_argument("path", nargs="?", default=None, help="Directory (default: cwd)")
    p_estimate.add_argument("--json", action="store_true", help="Output as JSON")

    # --- serve ---
    p_serve = sub.add_parser("serve", help="Start the MCP or HTTP server")
    p_serve.add_argument("--http", action="store_true", help="Run as HTTP REST API instead of MCP")
    p_serve.add_argument("--port", type=int, default=8080, help="HTTP port (default: 8080)")

    return parser


def main() -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    # Handle -c shorthand for count output mode
    if args.command == "search" and getattr(args, "output_mode_count", False):
        args.output_mode = "count"

    if args.command == "serve":
        sys.exit(_cmd_serve(args))
    elif args.command == "search":
        sys.exit(asyncio.run(_cmd_search(args)))
    elif args.command == "index":
        sys.exit(asyncio.run(_cmd_index(args)))
    elif args.command == "estimate":
        sys.exit(asyncio.run(_cmd_estimate(args)))
