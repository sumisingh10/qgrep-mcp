"""FastMCP server with tool definitions and pre-session index warming.

On startup, scans the cache directory for previously-indexed repos and rebuilds
any with stale indexes in the background. This way the first search of a new
session hits a fresh index instead of triggering a synchronous rebuild.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from .config import CACHE_DIR, has_qgrep
from .estimator import CostEstimator
from .index import (
    IndexMetadata,
    build_index,
    delete_index,
    has_index,
    index_status,
    is_index_stale,
)
from .search import SearchOrchestrator

logger = logging.getLogger(__name__)


async def warm_stale_indexes() -> None:
    """Scan cached index metadata and rebuild any stale indexes.

    Iterates over all per-repo cache directories, loads their metadata,
    and rebuilds indexes whose source files have been modified since the
    last build. Errors are logged and silently skipped.
    """
    if not CACHE_DIR.exists():
        return
    for entry in CACHE_DIR.iterdir():
        if not entry.is_dir():
            continue
        meta_file = entry / "index_meta.json"
        if not meta_file.exists():
            continue
        meta = IndexMetadata.load_from_file(meta_file)
        if meta is None:
            continue
        repo_path = meta.repo_path
        if not os.path.isdir(repo_path):
            continue
        if is_index_stale(repo_path):
            try:
                logger.info("Warming stale index for %s", repo_path)
                await build_index(repo_path)
            except RuntimeError as e:
                logger.warning("Failed to warm index for %s: %s", repo_path, e)


@asynccontextmanager
async def lifespan(app: FastMCP):
    """Server lifespan: warm stale indexes on startup."""
    task = asyncio.create_task(warm_stale_indexes())
    yield {}
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


mcp = FastMCP("qgrep-mcp", lifespan=lifespan)

estimator = CostEstimator()
orchestrator = SearchOrchestrator(estimator)


@mcp.tool()
async def search_code(
    pattern: str,
    path: str,
    glob: str | None = None,
    case_insensitive: bool = False,
    output_mode: str = "content",
    context_lines: int = 0,
    max_results: int = 200,
) -> dict:
    """Fast indexed code search. Searches file contents using a pre-built index when available, falling back to ripgrep otherwise. Up to 300x faster than standard grep on large codebases (10k+ files). Supports full regex syntax.

    Use this tool to search for patterns in code across a directory tree. Returns matching lines with file paths and line numbers. Automatically builds and maintains search indexes for large repositories to accelerate repeated searches.

    Args:
        pattern: Regular expression pattern to search for in file contents (e.g. "def main", "class\\s+\\w+", "TODO|FIXME").
        path: Absolute path to the directory to search in.
        glob: Glob pattern to filter files (e.g. "*.py", "*.{ts,tsx}"). When set, uses ripgrep directly.
        case_insensitive: Case-insensitive search.
        output_mode: "content" shows matching lines with context, "files_with_matches" lists file paths only, "count" shows per-file match counts.
        context_lines: Number of lines to show before and after each match.
        max_results: Maximum number of result lines to return.
    """
    path = os.path.expanduser(path)
    path = os.path.realpath(path)

    result = await orchestrator.search(
        pattern,
        path,
        glob=glob,
        case_insensitive=case_insensitive,
        output_mode=output_mode,
        context_lines=context_lines,
        max_results=max_results,
    )

    resp = {
        "matches": result.matches,
        "file_count": result.file_count,
        "match_count": result.match_count,
        "backend": result.backend,
        "elapsed_seconds": result.elapsed_seconds,
    }
    if result.truncated:
        resp["truncated"] = True
    if result.error:
        resp["error"] = result.error
    return resp


@mcp.tool()
async def build_search_index(
    action: str,
    path: str,
) -> dict:
    """Build or manage a search index for a codebase. Building an index makes search_code up to 300x faster on large repos. The index is persistent and only needs to be built once per repo.

    Args:
        action: One of "build" (create index), "rebuild" (delete and recreate), "status" (check if indexed), "delete" (remove index).
        path: Absolute path to the directory to index.
    """
    path = os.path.expanduser(path)
    path = os.path.realpath(path)

    if action == "status":
        return await index_status(path)

    if action == "delete":
        deleted = await delete_index(path)
        return {"deleted": deleted, "path": path}

    if action in ("build", "rebuild"):
        if action == "rebuild":
            await delete_index(path)
        try:
            meta = await build_index(path)
            estimator.record_build_time(path, meta.build_time_seconds)
            return {
                "success": True,
                "path": path,
                "project_name": meta.project_name,
                "build_time_seconds": meta.build_time_seconds,
            }
        except RuntimeError as e:
            return {"success": False, "error": str(e)}

    return {"error": f"Unknown action: {action}. Use build/rebuild/status/delete."}


@mcp.tool()
async def search_estimate(
    path: str,
) -> dict:
    """Analyze a codebase and recommend whether building a search index is worthwhile. Returns file count, current search latency stats, and a recommendation.

    Args:
        path: Absolute path to the directory to analyze.
    """
    from .ripgrep import count_files

    path = os.path.expanduser(path)
    path = os.path.realpath(path)

    # Ensure file count is fresh
    fc = await count_files(path)
    estimator.record_file_count(path, fc)

    rec = estimator.estimate(
        path, has_index=has_index(path), has_qgrep=has_qgrep()
    )

    return {
        "recommendation": rec.action,
        "confidence": rec.confidence,
        "reasoning": rec.reasoning,
        **rec.stats,
    }


def main() -> None:
    """Entry point for the MCP server."""
    mcp.run(transport="stdio")
