"""Lightweight REST API for qgrep-mcp.

Exposes the same search engine as the MCP server via plain HTTP endpoints.
No MCP client required — just curl or any HTTP library.

Usage:
    python -m qgrep_mcp --http              # localhost:8080
    python -m qgrep_mcp --http --port 9000  # custom port

Endpoints:
    POST /search          — search code (same params as search_code MCP tool)
    POST /index           — manage indexes (build/rebuild/status/delete)
    GET  /estimate?path=  — get indexing recommendation for a directory
    GET  /health          — server health check
"""

import asyncio
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from .config import has_qgrep
from .estimator import CostEstimator
from .index import build_index, delete_index, has_index, index_status
from .ripgrep import count_files
from .search import SearchOrchestrator

estimator = CostEstimator()
orchestrator = SearchOrchestrator(estimator)


def _run_async(coro):
    """Run an async coroutine from sync context."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, coro).result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


class SearchAPIHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the search API."""

    def do_GET(self):
        """Handle GET requests for /health and /estimate."""
        parsed = urlparse(self.path)

        if parsed.path == "/health":
            self._json_response({"status": "ok", "has_qgrep": has_qgrep()})
            return

        if parsed.path == "/estimate":
            params = parse_qs(parsed.query)
            path = params.get("path", [None])[0]
            if not path:
                self._json_response({"error": "path parameter required"}, status=400)
                return
            path = os.path.expanduser(path)
            path = os.path.realpath(path)
            result = _run_async(self._estimate(path))
            self._json_response(result)
            return

        self._json_response({"error": "not found"}, status=404)

    def do_POST(self):
        """Handle POST requests for /search and /index."""
        parsed = urlparse(self.path)
        body = self._read_body()
        if body is None:
            return

        if parsed.path == "/search":
            result = _run_async(self._search(body))
            self._json_response(result)
            return

        if parsed.path == "/index":
            result = _run_async(self._index(body))
            self._json_response(result)
            return

        self._json_response({"error": "not found"}, status=404)

    async def _search(self, body: dict) -> dict:
        """Execute a search request."""
        pattern = body.get("pattern")
        path = body.get("path")
        if not pattern or not path:
            return {"error": "pattern and path are required"}

        path = os.path.expanduser(path)
        path = os.path.realpath(path)

        result = await orchestrator.search(
            pattern,
            path,
            glob=body.get("glob"),
            case_insensitive=body.get("case_insensitive", False),
            output_mode=body.get("output_mode", "content"),
            context_lines=body.get("context_lines", 0),
            max_results=body.get("max_results", 200),
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

    async def _index(self, body: dict) -> dict:
        """Execute an index management request."""
        action = body.get("action")
        path = body.get("path")
        if not action or not path:
            return {"error": "action and path are required"}

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

    async def _estimate(self, path: str) -> dict:
        """Execute an estimate request."""
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

    def _read_body(self) -> dict | None:
        """Read and parse JSON request body."""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._json_response({"error": "request body required"}, status=400)
            return None
        try:
            raw = self.rfile.read(content_length)
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            self._json_response({"error": "invalid JSON"}, status=400)
            return None

    def _json_response(self, data: dict, status: int = 200):
        """Send a JSON response."""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode())

    def log_message(self, format, *args):
        """Suppress default request logging for cleaner output."""
        pass


def run_http(host: str = "127.0.0.1", port: int = 8080) -> None:
    """Start the REST API server."""
    server = HTTPServer((host, port), SearchAPIHandler)
    print(f"qgrep-mcp REST API running on http://{host}:{port}")
    print(f"  POST /search    — search code")
    print(f"  POST /index     — manage indexes")
    print(f"  GET  /estimate  — get recommendation")
    print(f"  GET  /health    — health check")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()
