# qgrep-mcp

Indexed code search MCP server + Claude Code plugin. Up to **237x faster** than ripgrep on large codebases.

An amortized cost estimator decides at query time whether building a qgrep index is worth it, based on file count (which correlates r=0.96 with ripgrep latency). Works fully without qgrep installed. It's a pure enhancement over ripgrep.

## Motivation

AI coding tools ship with ripgrep or similar linear-scan search. This works fine on small repos, but breaks down on large codebases:

- **rust-lang/rust** (58k files): ripgrep takes ~2.9s per search
- **Linux kernel** (80k+ files), **Chromium** (300k+ files), **Android** (500k+ files): even worse

An AI agent doing exploratory work might run 20-50 searches in a single session. At 3s each, that's 1-2.5 minutes of just waiting for grep. With an index, the same searches complete in ~0.2s total.

**Why not just fix it upstream?** The underlying models (Claude, GPT-4, etc.) are post-trained with tool-use behavior that favors built-in tools like Grep and file search. Cursor, Copilot, and similar IDEs are wrappers around these models, and the tool preferences are baked into the model weights during post-training. Changing which tools a model reaches for requires retraining cycles that take months. System prompts reinforce this further by defining the built-in tool set, and users can't modify them.

Even when an MCP tool like `search_code` is registered, the model defaults to the tools it was trained on. We tested this directly: Claude Code ignores `search_code` 100% of the time when only the MCP server is present, with no steering mechanism.

**This project bridges that gap** by working at the layer users can control: hooks intercept tool calls before they execute, skills inject context that nudges model behavior at inference time, and agents constrain tool access so indexed search is the only option. No model retraining needed, no system prompt changes, no waiting for upstream fixes.

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

### Option 1: Hook + MCP Server (hard redirect)

The fully automatic route. The **hook** intercepts every Grep call and redirects to `search_code` when it detects a large codebase. Claude doesn't need to be nudged or told anything; the hook handles it transparently.

```bash
git clone https://github.com/sumisingh10/qgrep-mcp.git
claude --plugin-dir ./qgrep-mcp
```

Then strip the skill and agent (the hook makes them redundant):
```bash
rm -rf ./qgrep-mcp/skills/ ./qgrep-mcp/agents/
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

The skill prompt lives at [`skills/code-search/SKILL.md`](skills/code-search/SKILL.md). You can customize the trigger phrases or tool guidance there.

```bash
git clone https://github.com/sumisingh10/qgrep-mcp.git
claude --plugin-dir ./qgrep-mcp
```

Then strip the hook and agent:
```bash
rm -rf ./qgrep-mcp/hooks/ ./qgrep-mcp/agents/
```

### Option 3: Agent + MCP Server (delegated search)

Loads the **agent** and **MCP server**. Claude can spawn the `code-search` agent for search-heavy tasks. The agent only has access to `search_code`, `build_search_index`, `search_estimate`, `Read`, and `Glob` (no built-in Grep), so it always uses indexed search.

Useful for exploratory tasks across large codebases where you want search delegated to a subagent that runs multiple indexed queries in parallel.

The agent definition lives at [`agents/code-search.md`](agents/code-search.md). You can adjust the tool list, model, or instructions there.

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

## Using with other AI coding tools

The MCP server is the portable core. The hook, skill, and agent are Claude Code-specific steering mechanisms, but the server itself works with any MCP-compatible client.

| Layer | Claude-specific? | Portable? |
|-------|-----------------|-----------|
| MCP Server (`search_code`, etc.) | No | Any MCP client |
| Hook (`hooks/intercept_grep.py`) | Yes (PreToolUse) | No |
| Skill (`skills/code-search/SKILL.md`) | Yes (Claude plugin) | No |
| Agent (`agents/code-search.md`) | Yes (Claude plugin) | No |

> **Note:** Unlike Claude Code, most other tools don't have a built-in grep that takes priority over MCP tools. The MCP server alone may work fine without needing a hook or skill to steer the tool toward it.

### Cross-tool concepts

Each AI coding tool has its own version of instruction files and MCP configuration:

| Concept | Claude Code | Codex CLI | Cursor | Copilot (VS Code) |
|---------|------------|-----------|--------|-------------------|
| Instruction file | `CLAUDE.md` | `AGENTS.md` | `.cursor/rules/*.mdc` | `.github/copilot-instructions.md` |
| MCP config | `.mcp.json` | `~/.codex/config.toml` | Settings UI | `.vscode/mcp.json` |
| Skills/nudges | `skills/*/SKILL.md` | `.agents/skills/*/SKILL.md` | Rules (glob-triggered) | N/A |
| Custom agents | `agents/*.md` | N/A | N/A | N/A |

### OpenAI Codex CLI

```bash
pip install -e ./qgrep-mcp
```

Add to `~/.codex/config.toml`:
```toml
[mcp_servers.qgrep-mcp]
command = "python3"
args = ["-m", "qgrep_mcp"]
```

Codex also supports skills in the same directory structure. You can adapt the skill prompt from [`skills/code-search/SKILL.md`](skills/code-search/SKILL.md) into `.agents/skills/qgrep-search/SKILL.md` in your project to nudge Codex toward `search_code`.

### Cursor

Add to `.cursor/mcp.json` in your project:
```json
{
  "qgrep-mcp": {
    "command": "python3",
    "args": ["-m", "qgrep_mcp"]
  }
}
```

You can also create a `.cursor/rules/qgrep-search.mdc` rule to nudge Cursor toward the MCP tool. Adapt the prompt from [`skills/code-search/SKILL.md`](skills/code-search/SKILL.md).

### GitHub Copilot (VS Code)

Add to `.vscode/mcp.json`:
```json
{
  "servers": {
    "qgrep-mcp": {
      "command": "python3",
      "args": ["-m", "qgrep_mcp"]
    }
  }
}
```

### Any MCP-compatible client

Install the package, then point your client at the stdio server:
```bash
pip install -e ./qgrep-mcp
python3 -m qgrep_mcp
```

All tools listed above require `pip install -e ./qgrep-mcp` first so `python3 -m qgrep_mcp` resolves without needing a `PYTHONPATH` override.

## Running tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

26 tests covering the estimator, search orchestrator, index management, and hook logic.
