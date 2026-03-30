# qgrep-mcp

Indexed code search MCP server + Claude Code plugin. Up to **237x faster** than ripgrep on large codebases.

An amortized cost estimator decides at query time whether building a qgrep index is worth it, based on file count (which correlates r=0.96 with ripgrep latency). Works fully without qgrep installed — it's a pure enhancement over ripgrep.

## Benchmarks

Tested on [rust-lang/rust](https://github.com/rust-lang/rust) (58,534 files):

| Query | ripgrep | qgrep | Speedup |
|-------|---------|-------|---------|
| `unsafe impl` | 2.88s | 0.010s | 277x |
| `TODO\|FIXME\|HACK` | 2.92s | 0.018s | 161x |
| `pub async fn` | 2.88s | 0.011s | 267x |
| `impl Iterator for` | 2.86s | 0.012s | 243x |
| `fn main` | 3.02s | 0.011s | 288x |
| **Average** | **2.91s** | **0.012s** | **237x** |

Index build time: **7.4s** (one-time cost, pays for itself after ~3 searches)

### What determines search speed?

File count is the dominant factor — not total file size:

| Directory | Files | Size (MB) | rg latency |
|-----------|-------|-----------|------------|
| rust/compiler | 2,804 | 31.6 | 0.16s |
| rust/src/tools | 11,107 | 44.7 | 0.59s |
| rust/src | 12,384 | 68.9 | 2.35s |
| rust/tests | 41,119 | 49.7 | 10.4s |
| rust (full) | 58,534 | 194.4 | 25.8s |

File count vs latency correlation: **0.959**. Total size vs latency: 0.024.

## Installation

### Option 1: Claude Code Plugin (recommended)

Installs the hook that transparently intercepts Grep on large repos + the MCP server.

```bash
/plugin marketplace add sumisingh10/qgrep-mcp
/plugin install qgrep-mcp@sumisingh10
```

### Option 2: MCP Server Only

```bash
pip install qgrep-mcp  # or: pip install -e ".[dev]"
claude mcp add qgrep-mcp -- python -m qgrep_mcp
```

### Prerequisites

- **ripgrep** — usually already available (Claude Code bundles it)
- **qgrep** — optional but recommended for the speed gains. Install from [releases](https://github.com/zeux/qgrep/releases):
  ```bash
  # macOS
  curl -sL https://github.com/zeux/qgrep/releases/download/v1.5/qgrep-macos.zip -o /tmp/qgrep.zip
  unzip -o /tmp/qgrep.zip -d /tmp && chmod +x /tmp/qgrep && sudo mv /tmp/qgrep /usr/local/bin/
  ```

## How it works

### Plugin mode (Option 1)

1. Claude calls Grep normally
2. A `PreToolUse` hook intercepts, checks file count:
   - **< 5k files** — Grep runs as normal (ripgrep is fast enough)
   - **5k-15k files** — allows first 2 Grep calls to measure latency, then redirects if slow
   - **> 15k files** — redirects immediately to `search_code` MCP tool
3. `search_code` auto-builds a qgrep index on first call, then searches in milliseconds
4. All subsequent searches use the index

### MCP Server mode (Option 2)

Three tools are exposed:

| Tool | Description |
|------|-------------|
| `search_code` | Fast indexed code search with auto-selected backend |
| `build_search_index` | Manage index lifecycle (build/rebuild/status/delete) |
| `search_estimate` | Get indexing recommendation + stats for a directory |

The estimator handles backend selection:
- Small repos (< 5k files): always ripgrep
- Large repos (> 15k files): build index immediately, use qgrep
- Gray zone (5k-15k): collect latency baselines, index if rg > 1s average
- Features qgrep can't handle (context lines, glob filters) always use ripgrep

## Running tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

26 tests covering the estimator, search orchestrator, index management, and hook logic.
