# qgrep-mcp

Indexed code search MCP server + Claude Code plugin. Up to **237x faster** than ripgrep on large codebases.

An amortized cost estimator decides at query time whether building a qgrep index is worth it, based on file count (which correlates r=0.96 with ripgrep latency). Works fully without qgrep installed. It's a pure enhancement over ripgrep.

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

File count is the dominant factor, not total file size:

| Directory | Files | Size (MB) | rg latency |
|-----------|-------|-----------|------------|
| rust/compiler | 2,804 | 31.6 | 0.16s |
| rust/src/tools | 11,107 | 44.7 | 0.59s |
| rust/src | 12,384 | 68.9 | 2.35s |
| rust/tests | 41,119 | 49.7 | 10.4s |
| rust (full) | 58,534 | 194.4 | 25.8s |

File count vs latency correlation: **0.959**. Total size vs latency: 0.024.

## Installation

Four options, from most to least automated. Pick the one that fits your workflow:

### Option 1: Full Plugin (hook + skill + agent + MCP server)

The hard route. Installs everything: the **hook** transparently intercepts Grep on large repos, the **skill** nudges Claude contextually, the **agent** delegates search tasks, and the **MCP server** provides the indexed search tools. Claude will use indexed search automatically with zero user intervention.

```bash
/plugin marketplace add sumisingh10/qgrep-mcp
/plugin install qgrep-mcp@sumisingh10
```

**How the hook works:**
1. Claude calls Grep normally
2. Hook intercepts and checks file count:
   - **< 5k files** → Grep runs as normal (ripgrep is fast enough)
   - **5k-15k files** → allows first 2 Grep calls to measure latency, then redirects if slow
   - **> 15k files** → redirects immediately to `search_code` MCP tool
3. `search_code` auto-builds a qgrep index on first call, then searches in milliseconds
4. All subsequent searches use the index

### Option 2: Skill + MCP Server (soft nudge)

Loads the **skill** and **MCP server** but no hook. When Claude's task involves searching code ("find in files", "grep for", "search the codebase", etc.), the skill activates and nudges Claude toward `search_code`. Built-in Grep is not intercepted, so Claude may still use it for simple searches.

Zero overhead when the skill isn't triggered: only metadata (~100 words) is always loaded, the full body is injected only when relevant.

```bash
git clone https://github.com/sumisingh10/qgrep-mcp.git
claude --plugin-dir ./qgrep-mcp
```

Then remove the hook if you don't want it:
```bash
rm -rf ./qgrep-mcp/hooks/
```

### Option 3: Agent + MCP Server (delegated search)

Loads the **agent** and **MCP server**. Claude can spawn the `code-search` agent for search-heavy tasks. The agent only has access to `search_code`, `build_search_index`, `search_estimate`, `Read`, and `Glob` (no built-in Grep), so it always uses indexed search.

Useful for exploratory tasks across large codebases where you want search delegated to a subagent that runs multiple indexed queries in parallel.

```bash
git clone https://github.com/sumisingh10/qgrep-mcp.git
claude --plugin-dir ./qgrep-mcp
```

Then strip the hook and skill:
```bash
rm -rf ./qgrep-mcp/hooks/ ./qgrep-mcp/skills/
```

### Option 4: MCP Server only (not recommended)

Just the raw MCP tools. **Claude will not use these on its own.** It always prefers built-in Grep over MCP tools. This option only works if you explicitly tell Claude to use `search_code` in every prompt. Without a hook, skill, or agent to steer Claude toward indexed search, the MCP server sits unused.

```bash
pip install -e ./qgrep-mcp
claude mcp add qgrep-mcp -- python -m qgrep_mcp
```

> **Why not standalone?** We tested this across multiple sessions. Even with the MCP server registered, Claude defaults to built-in Grep 100% of the time. You need at least one steering mechanism (hook, skill, or agent) to make indexed search actually get used.

### Prerequisites

- **ripgrep** → usually already available (Claude Code bundles it)
- **qgrep** → optional but recommended for the speed gains. Install from [releases](https://github.com/zeux/qgrep/releases):
  ```bash
  # macOS
  curl -sL https://github.com/zeux/qgrep/releases/download/v1.5/qgrep-macos.zip -o /tmp/qgrep.zip
  unzip -o /tmp/qgrep.zip -d /tmp && chmod +x /tmp/qgrep && sudo mv /tmp/qgrep /usr/local/bin/
  ```

## MCP Server tools

| Tool | Description |
|------|-------------|
| `search_code` | Fast indexed code search with auto-selected backend |
| `build_search_index` | Manage index lifecycle (build/rebuild/status/delete) |
| `search_estimate` | Get indexing recommendation + stats for a directory |

The estimator handles backend selection:
- Small repos (< 5k files): always ripgrep
- Large repos (> 15k files): build index immediately, use qgrep
- Gray zone (5k-15k): collect latency baselines, index if rg > 1s average
- Features qgrep can't handle (context lines, glob filters): always use ripgrep

## Running tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

26 tests covering the estimator, search orchestrator, index management, and hook logic.
