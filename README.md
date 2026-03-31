# qgrep-mcp

Indexed code search MCP server + Claude Code plugin. Orders of magnitude faster than ripgrep on large codebases.

An amortized cost estimator decides at query time whether building a qgrep index is worth it, based on file count (which correlates r=0.96 with ripgrep latency). Works fully without qgrep installed. It's a pure enhancement over ripgrep.

## Motivation

AI coding tools ship with ripgrep or similar linear-scan search. This works fine on small repos, but breaks down on large codebases:

| Repository | Files | ripgrep (per search) | qgrep (per search) |
|-----------|-------|---------------------|-------------------|
| [home-assistant/core](https://github.com/home-assistant/core) | 24,718 | ~28s | ~0.034s |
| [rust-lang/rust](https://github.com/rust-lang/rust) | 58,547 | ~60s | ~0.034s |
| [torvalds/linux](https://github.com/torvalds/linux) | 92,920 | ~92s | ~0.161s |

Each search blocks the agent's reasoning until it returns. Even with async execution, ripgrep saturates disk I/O scanning the same files repeatedly. An indexed search returns in milliseconds regardless of repo size.

**Why not just fix it upstream?** The models behind these coding tools are post-trained to use specific built-in tools like Grep and file search. Tool preferences get baked into the model weights during post-training, and system prompts reinforce them further by defining the available tool set. Users can't modify either. Even when an MCP tool like `search_code` is registered alongside built-in Grep, the model defaults to what it was post-trained on. We tested this directly: Claude Code ignores `search_code` 100% of the time when only the MCP server is present, with no steering mechanism.

**This project bridges that gap** by working at the layer users can control: hooks intercept tool calls before they execute, skills inject context that nudges model behavior at inference time, and agents constrain tool access so indexed search is the only option. No post-training needed, no system prompt changes, no waiting for upstream fixes.

## Benchmarks

Tested on three real-world repos with **cold disk cache** (OS file cache cleared between runs, simulating a fresh session where the AI agent hasn't touched these files yet). This reflects real-world conditions since agents start fresh sessions and the OS evicts cached file data over time:

| Repository | Files | Avg ripgrep | Avg qgrep | Speedup | Index build |
|-----------|-------|-------------|-----------|---------|-------------|
| [home-assistant/core](https://github.com/home-assistant/core) | 24,718 | 27.6s | 0.034s | **812x** | 93s |
| [rust-lang/rust](https://github.com/rust-lang/rust) | 58,547 | 59.6s | 0.034s | **1,753x** | 83s |
| [torvalds/linux](https://github.com/torvalds/linux) | 92,920 | 92.4s | 0.161s | **574x** | 236s |

### Detailed results

**rust-lang/rust (58,547 files):**

| Query | ripgrep | qgrep | Speedup |
|-------|---------|-------|---------|
| `TODO\|FIXME` | 59.65s | 0.055s | 1,085x |
| `fn main` | 59.66s | 0.027s | 2,210x |
| `unsafe impl` | 59.40s | 0.018s | 3,300x |
| `fn\s+\w+\(.*Result<` | 36.85s | 0.037s | 990x |
| `pub\s+(unsafe\s+)?fn\s+\w+` | 34.89s | 0.041s | 861x |
| `#\[derive\(.*Clone.*\)\]` | 33.83s | 0.027s | 1,242x |

**Linux kernel (92,920 files):**

| Query | ripgrep | qgrep | Speedup |
|-------|---------|-------|---------|
| `TODO\|FIXME` | 65.04s | 0.312s | 208x |
| `int main` | 107.97s | 0.074s | 1,459x |
| `static void` | 104.30s | 0.098s | 1,064x |
| `static\s+const\s+struct\s+file_operations` | 49.85s | 0.258s | 193x |
| `pr_err\(\|pr_warn\(\|pr_info\(` | 47.66s | 0.225s | 212x |
| `MODULE_LICENSE\(` | 51.55s | 0.215s | 240x |

**home-assistant/core (24,718 files):**

| Query | ripgrep | qgrep | Speedup |
|-------|---------|-------|---------|
| `TODO\|FIXME` | 36.53s | 0.043s | 850x |
| `async def` | 22.96s | 0.036s | 638x |
| `class.*:` | 23.30s | 0.024s | 971x |
| `async\s+def\s+async_setup_entry` | 23.19s | 0.027s | 867x |
| `raise\s+HomeAssistantError` | 3.43s | 0.026s | 132x |
| `CONF_\w+\s*=\s*"` | 1.27s | 0.017s | 74x |

### What determines search speed?

File count is the dominant factor. Across our three benchmark repos (25k, 58k, 93k files), ripgrep latency scales nearly linearly with file count.

## Installation

Five options, from most to least automated. Pick the one that fits your workflow:

### Option 1: Hook + MCP Server (hard redirect)

The fully automatic route. The **hook** intercepts every Grep call and redirects to `search_code` when it detects a large codebase. Claude doesn't need to be nudged or told anything; the hook handles it transparently.

```bash
git clone https://github.com/sumisingh10/qgrep-mcp.git
claude --plugin-dir ./qgrep-mcp
```

The skill and agent are also included but have no effect when the hook is active. They're harmless to leave in place.

**How the hook works:**
1. Claude calls Grep normally
2. Hook intercepts and checks file count:
   - **< 5k files** → Grep runs as normal (ripgrep is fast enough even on cold cache)
   - **5k-15k files** → allows first 2 Grep calls to collect latency baselines, then redirects if slow
   - **> 15k files** → redirects immediately to `search_code` MCP tool
3. `search_code` auto-builds a qgrep index on first call, then searches in milliseconds
4. All subsequent searches use the index

### Option 2: MCP Server + CLAUDE.md (manual nudge)

Register the MCP server and add a line to your `CLAUDE.md` telling the model to prefer `search_code` over built-in Grep. No plugin, no hook, no skill files needed.

```bash
pip install -e ./qgrep-mcp
claude mcp add qgrep-mcp -- python -m qgrep_mcp
```

Then add to your project's `CLAUDE.md`:
```markdown
When searching code, prefer the `search_code` MCP tool over built-in Grep. It uses an indexed backend that is orders of magnitude faster on large codebases.
```

This works because `CLAUDE.md` is loaded into context at the start of every session. The same approach works with `AGENTS.md` (Codex), `.cursor/rules/*.mdc` (Cursor), or `.github/copilot-instructions.md` (Copilot).

### Option 3: Skill + MCP Server (soft nudge)

Loads the **skill** and **MCP server** but no hook. When Claude's task involves searching code ("find in files", "grep for", "search the codebase", etc.), the skill activates and nudges Claude toward `search_code`. Built-in Grep is not intercepted, so Claude may still use it for simple searches.

Zero overhead when the skill isn't triggered: only metadata (~100 words) is always loaded, the full body is injected only when relevant.

The skill prompt lives at [`skills/code-search/SKILL.md`](skills/code-search/SKILL.md). You can customize the trigger phrases or tool guidance there.

```bash
git clone https://github.com/sumisingh10/qgrep-mcp.git
claude --plugin-dir ./qgrep-mcp
```

The hook and agent are also included but unused by this option. They're harmless to leave in place.

### Option 4: Agent + MCP Server (delegated search)

Loads the **agent** and **MCP server**. Claude can spawn the `code-search` agent for search-heavy tasks. The agent only has access to `search_code`, `build_search_index`, `search_estimate`, `Read`, and `Glob` (no built-in Grep), so it always uses indexed search.

Useful for exploratory tasks across large codebases where you want search delegated to a subagent that runs multiple indexed queries in parallel.

The agent definition lives at [`agents/code-search.md`](agents/code-search.md). You can adjust the tool list, model, or instructions there.

```bash
git clone https://github.com/sumisingh10/qgrep-mcp.git
claude --plugin-dir ./qgrep-mcp
```

The hook and skill are also included but unused by this option. They're harmless to leave in place.

### Option 5: MCP Server only (not recommended)

Just the raw MCP tools with no steering. **The model will not use these on its own.** It always prefers built-in Grep over MCP tools. This option only works if you explicitly ask for `search_code` in every prompt.

```bash
pip install -e ./qgrep-mcp
claude mcp add qgrep-mcp -- python -m qgrep_mcp
```

> **Why not standalone?** We tested this across multiple sessions. Even with the MCP server registered, Claude defaults to built-in Grep 100% of the time. You need at least one steering mechanism to make indexed search actually get used.

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
- Small repos (< 5k files): always ripgrep (fast enough even on cold cache)
- Large repos (> 15k files): build index immediately, use qgrep
- Gray zone (5k-15k): collects latency baselines over the first 2 searches, indexes if ripgrep is consistently slow
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

## Acknowledgments

Thanks to Michael Sklar and Derek Feriancek for helping me work through the core problem: retraining models to prefer new tools is not feasible for everyone, and users can't modify system prompts. A runtime harness that sits between the model and its built-in tools is one abstracted solution to that.
