"""Async ripgrep wrapper and SearchResult dataclass."""

import asyncio
import time
from dataclasses import dataclass, field

from .config import find_binary


@dataclass
class SearchResult:
    """Unified search result returned by both ripgrep and qgrep backends.

    Contains the matched lines, counts, backend name, elapsed time, and
    whether the results were truncated due to max_results.
    """
    matches: list[str] = field(default_factory=list)
    file_count: int = 0
    match_count: int = 0
    backend: str = "ripgrep"
    elapsed_seconds: float = 0.0
    truncated: bool = False
    error: str | None = None


async def count_files(path: str) -> int:
    """Count files in a directory using rg --files."""
    rg = find_binary("rg")
    if not rg:
        # Fallback: rough count via find
        proc = await asyncio.create_subprocess_exec(
            "find", path, "-type", "f", "-not", "-path", "*/.git/*",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        return stdout.count(b"\n")
    proc = await asyncio.create_subprocess_exec(
        rg, "--files", path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    return stdout.count(b"\n")


async def ripgrep_search(
    pattern: str,
    path: str,
    *,
    glob: str | None = None,
    case_insensitive: bool = False,
    output_mode: str = "content",
    context_lines: int = 0,
    max_results: int = 200,
) -> SearchResult:
    """Run ripgrep and return SearchResult."""
    rg = find_binary("rg")
    if not rg:
        return SearchResult(error="ripgrep (rg) not found on PATH")

    args = [rg, "--no-heading", "--with-filename", "--line-number"]

    if case_insensitive:
        args.append("-i")
    if glob:
        args.extend(["--glob", glob])
    if context_lines > 0:
        args.extend(["-C", str(context_lines)])

    if output_mode == "files_with_matches":
        args.append("--files-with-matches")
    elif output_mode == "count":
        args.append("--count")

    args.extend(["--max-count", str(max_results)])
    args.append(pattern)
    args.append(path)

    start = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    elapsed = time.monotonic() - start

    lines = stdout.decode(errors="replace").splitlines()

    # Truncate output
    truncated = len(lines) > max_results
    lines = lines[:max_results]

    # Count unique files
    files_seen: set[str] = set()
    for line in lines:
        if ":" in line:
            files_seen.add(line.split(":")[0])

    return SearchResult(
        matches=lines,
        file_count=len(files_seen),
        match_count=len(lines),
        backend="ripgrep",
        elapsed_seconds=round(elapsed, 4),
        truncated=truncated,
        error=stderr.decode(errors="replace").strip() if proc.returncode not in (0, 1) else None,
    )
