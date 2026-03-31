"""Configuration, path helpers, and binary detection for qgrep-mcp."""

import hashlib
import os
import shutil
from pathlib import Path

CACHE_DIR = Path(os.environ.get("QGREP_MCP_CACHE", Path.home() / ".cache" / "qgrep-mcp"))
STATS_FILE = CACHE_DIR / "stats.json"
LATENCY_WINDOW = 20
SESSION_SEARCH_RESET = True  # reset per server start

# Thresholds (empirical: file count r=0.96 with rg latency)
SMALL_REPO_THRESHOLD = 5000    # below this, rg < 0.5s — never index
LARGE_REPO_THRESHOLD = 15000   # above this, rg > 5s — always index
COLD_START_SEARCHES = 2        # gray zone (5k-15k): measure this many before deciding
RG_SLOW_THRESHOLD = 1.0        # seconds — if rg averages above this in gray zone, index


def repo_hash(path: str) -> str:
    """Deterministic hash for a repo path."""
    return hashlib.sha256(os.path.realpath(path).encode()).hexdigest()[:16]


def repo_cache_dir(path: str) -> Path:
    """Per-repo cache directory."""
    d = CACHE_DIR / repo_hash(path)
    d.mkdir(parents=True, exist_ok=True)
    return d


def qgrep_project_name(path: str) -> str:
    """qgrep project name for a repo."""
    return f"qmcp_{repo_hash(path)}"


def _find_rg_fallback() -> str | None:
    """Search for ripgrep in common locations (Claude Code vendor, homebrew, etc.)."""
    import glob as globmod
    import platform

    arch = platform.machine()
    arch_map = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64", "AMD64": "x64"}
    arch_key = arch_map.get(arch, arch)
    system = platform.system().lower()  # "darwin" or "linux"

    patterns = [
        # Claude Code vendor paths
        f"/opt/homebrew/lib/node_modules/@anthropic-ai/claude-code/vendor/ripgrep/{arch_key}-{system}/rg",
        f"/usr/local/lib/node_modules/@anthropic-ai/claude-code/vendor/ripgrep/{arch_key}-{system}/rg",
        os.path.expanduser(f"~/.local/share/claude/versions/*/vendor/ripgrep/{arch_key}-{system}/rg"),
        # Global node_modules wildcard
        f"/opt/homebrew/lib/node_modules/@anthropic-ai/*/vendor/ripgrep/{arch_key}-{system}/rg",
    ]
    for pattern in patterns:
        matches = sorted(globmod.glob(pattern), reverse=True)
        for m in matches:
            if os.path.isfile(m) and os.access(m, os.X_OK):
                return m
    return None


def find_binary(name: str) -> str | None:
    """Find a binary on PATH, with fallback search for ripgrep."""
    result = shutil.which(name)
    if result:
        return result
    if name == "rg":
        return _find_rg_fallback()
    return None


def has_qgrep() -> bool:
    """Check whether the qgrep binary is available on the system."""
    return find_binary("qgrep") is not None


def has_ripgrep() -> bool:
    """Check whether the ripgrep (rg) binary is available on the system."""
    return find_binary("rg") is not None
