#!/usr/bin/env python3
"""
PreToolUse hook that intercepts Grep calls on large codebases
and redirects Claude to use the search_code MCP tool instead.

Decision logic (based on empirical benchmarks — file count r=0.96 with rg latency):
  - < 5k files:   always allow Grep (rg < 0.5s)
  - 5k-15k files: allow first 2 Grep calls to measure, then redirect
  - > 15k files:  redirect immediately (rg > 5s, indexing always wins)
  - index exists:  always redirect regardless of size

Stats and file counts are cached in ~/.cache/qgrep-mcp/stats.json
to avoid recounting on every call.
"""

import hashlib
import json
import os
import subprocess
import sys
import time

CACHE_DIR = os.path.expanduser(os.environ.get("QGREP_MCP_CACHE", "~/.cache/qgrep-mcp"))
STATS_FILE = os.path.join(CACHE_DIR, "stats.json")

# Thresholds (from benchmark: file count correlates 0.96 with rg latency)
SMALL_THRESHOLD = 5000       # below this, rg is always fast enough
LARGE_THRESHOLD = 15000      # above this, always redirect
GRAY_ZONE_CALLS = 2          # in the 5k-15k zone, allow this many before redirecting


def repo_hash(path: str) -> str:
    return hashlib.sha256(os.path.realpath(path).encode()).hexdigest()[:16]


def load_stats() -> dict:
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_stats(data: dict) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = STATS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, STATS_FILE)


def count_files_fast(path: str) -> int:
    """Quick file count. Tries rg --files first, falls back to find."""
    for cmd in [
        ["rg", "--files", path],
        ["/usr/local/bin/rg", "--files", path],
    ]:
        try:
            result = subprocess.run(
                cmd, capture_output=True, timeout=10
            )
            if result.returncode == 0:
                return result.stdout.count(b"\n")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    # Also check Claude Code's vendored rg
    import glob as globmod
    import platform
    arch = {"arm64": "arm64", "x86_64": "x64"}.get(platform.machine(), platform.machine())
    system = platform.system().lower()
    for pattern in [
        f"/opt/homebrew/lib/node_modules/@anthropic-ai/claude-code/vendor/ripgrep/{arch}-{system}/rg",
        f"/opt/homebrew/lib/node_modules/@anthropic-ai/*/vendor/ripgrep/{arch}-{system}/rg",
    ]:
        for rg_path in globmod.glob(pattern):
            try:
                result = subprocess.run(
                    [rg_path, "--files", path], capture_output=True, timeout=10
                )
                if result.returncode == 0:
                    return result.stdout.count(b"\n")
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue

    # Last resort: find
    try:
        result = subprocess.run(
            ["find", path, "-type", "f", "-not", "-path", "*/.git/*"],
            capture_output=True, timeout=15
        )
        return result.stdout.count(b"\n")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 0


def has_index(path: str) -> bool:
    """Check if a qgrep index exists for this path."""
    h = repo_hash(path)
    meta_file = os.path.join(CACHE_DIR, h, "index_meta.json")
    return os.path.exists(meta_file)


def resolve_search_path(tool_input: dict) -> str | None:
    """Extract the search directory from Grep tool input."""
    path = tool_input.get("path", "")
    if not path:
        return None
    path = os.path.expanduser(path)
    path = os.path.realpath(path)
    if os.path.isfile(path):
        path = os.path.dirname(path)
    return path


def build_redirect_message(path: str, file_count: int, has_idx: bool) -> str:
    """Construct the stderr message that tells Claude to use search_code."""
    reason = ""
    if has_idx:
        reason = f"A search index exists for this directory ({file_count:,} files)."
    else:
        reason = f"This directory has {file_count:,} files — ripgrep will be slow."

    return (
        f"{reason} "
        f"Use the search_code MCP tool instead — it's up to 300x faster on large codebases. "
        f"Call search_code with the same pattern and path={path}"
    )


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw)
    except (json.JSONDecodeError, IOError):
        sys.exit(0)  # Can't parse input, allow Grep

    tool_name = data.get("tool_name", "")
    if tool_name != "Grep":
        sys.exit(0)

    tool_input = data.get("tool_input", {})
    path = resolve_search_path(tool_input)
    if not path or not os.path.isdir(path):
        sys.exit(0)  # Can't resolve path, allow Grep

    h = repo_hash(path)

    # Check if index already exists — always redirect
    if has_index(path):
        stats = load_stats()
        fc = stats.get(h, {}).get("file_count", 0)
        print(build_redirect_message(path, fc, True), file=sys.stderr)
        sys.exit(2)

    # Get or compute file count
    stats = load_stats()
    repo_stats = stats.get(h, {})
    fc = repo_stats.get("file_count", 0)

    if fc == 0:
        fc = count_files_fast(path)
        if h not in stats:
            stats[h] = {}
        stats[h]["file_count"] = fc
        stats[h]["last_counted"] = time.time()
        save_stats(stats)

    # Decision logic
    if fc < SMALL_THRESHOLD:
        # Small repo — always let Grep through
        sys.exit(0)

    if fc >= LARGE_THRESHOLD:
        # Large repo — redirect immediately
        print(build_redirect_message(path, fc, False), file=sys.stderr)
        sys.exit(2)

    # Gray zone (5k-15k files): allow first N calls, then redirect
    grep_calls = repo_stats.get("grep_calls", 0) + 1
    stats[h]["grep_calls"] = grep_calls
    save_stats(stats)

    if grep_calls <= GRAY_ZONE_CALLS:
        sys.exit(0)  # Still collecting data
    else:
        print(build_redirect_message(path, fc, False), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
