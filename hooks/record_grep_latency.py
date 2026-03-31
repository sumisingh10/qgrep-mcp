#!/usr/bin/env python3
"""
PostToolUse hook that records observed Grep latency into the shared stats file.

When Claude's built-in Grep completes, this hook captures the elapsed time
and feeds it into the estimator's stats.json. This improves gray zone decisions
(5k-15k files) by using actual observed latency rather than relying solely on
file count heuristics.

Reads the tool result JSON from stdin. Expects tool_name == "Grep" and
extracts the search path from tool_input. Duration is calculated from the
tool_result's timing metadata if available, otherwise skipped.
"""

import hashlib
import json
import os
import sys
import time

CACHE_DIR = os.path.expanduser(os.environ.get("QGREP_MCP_CACHE", "~/.cache/qgrep-mcp"))
STATS_FILE = os.path.join(CACHE_DIR, "stats.json")
LATENCY_WINDOW = 20


def repo_hash(path: str) -> str:
    """Deterministic short hash for a repo path."""
    return hashlib.sha256(os.path.realpath(path).encode()).hexdigest()[:16]


def load_stats() -> dict:
    """Load cached stats from disk."""
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_stats(data: dict) -> None:
    """Atomically persist stats to disk."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = STATS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, STATS_FILE)


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


def main():
    """Entry point for the PostToolUse hook. Records Grep latency to stats."""
    try:
        raw = sys.stdin.read()
        data = json.loads(raw)
    except (json.JSONDecodeError, IOError):
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    if tool_name != "Grep":
        sys.exit(0)

    tool_input = data.get("tool_input", {})
    path = resolve_search_path(tool_input)
    if not path or not os.path.isdir(path):
        sys.exit(0)

    # Extract duration from tool result metadata
    tool_result = data.get("tool_result", {})
    duration = tool_result.get("duration_seconds")
    if duration is None:
        # Try to parse from timing info if present
        duration = tool_result.get("elapsed_seconds")
    if duration is None:
        sys.exit(0)

    try:
        duration = float(duration)
    except (ValueError, TypeError):
        sys.exit(0)

    # Record the latency
    h = repo_hash(path)
    stats = load_stats()
    if h not in stats:
        stats[h] = {}

    rg_latencies = stats[h].get("rg_latencies", [])
    rg_latencies.append(duration)
    stats[h]["rg_latencies"] = rg_latencies[-LATENCY_WINDOW:]
    stats[h]["last_updated"] = time.time()
    save_stats(stats)


if __name__ == "__main__":
    main()
