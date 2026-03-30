# qgrep-mcp

Indexed code search MCP server with intelligent ripgrep fallback.

An **amortized cost estimator** decides at query time whether building a qgrep index is worth it based on repo size, search frequency, and latency history. Works fully without qgrep installed — it's a pure enhancement over ripgrep.

## Installation

```bash
pip install -e ".[dev]"
```

## Register with Claude Code

```bash
claude mcp add qgrep-mcp -- python -m qgrep_mcp
```

## Tools

| Tool | Description |
|------|-------------|
| `qgrep_search` | Search code with auto-selected backend |
| `qgrep_index` | Manage index lifecycle (build/rebuild/status/delete) |
| `qgrep_estimate` | Get indexing recommendation + stats |

## How it works

1. First few searches always use ripgrep to collect latency baselines
2. The estimator tracks file counts, search frequency, and latency per repo
3. When indexing would amortize its build cost, it recommends (or auto-builds) a qgrep index
4. Features qgrep can't handle (context lines, glob filters) always use ripgrep

## Running tests

```bash
pytest tests/
```
