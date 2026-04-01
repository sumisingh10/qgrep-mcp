"""Tests for the REST API server."""

import json
import threading
import time
from http.client import HTTPConnection

import pytest

from qgrep_mcp import config


@pytest.fixture(autouse=True)
def tmp_cache(tmp_path, monkeypatch):
    """Redirect cache to a temp directory."""
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(config, "STATS_FILE", tmp_path / "stats.json")
    return tmp_path


@pytest.fixture
def api_server(tmp_cache):
    """Start the REST API on a random-ish port and yield a connection."""
    from qgrep_mcp.api import run_http
    from http.server import HTTPServer
    from qgrep_mcp.api import SearchAPIHandler

    server = HTTPServer(("127.0.0.1", 0), SearchAPIHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.1)
    conn = HTTPConnection("127.0.0.1", port, timeout=30)
    yield conn
    server.shutdown()
    conn.close()


class TestHealthEndpoint:
    def test_health(self, api_server):
        """GET /health returns ok."""
        api_server.request("GET", "/health")
        resp = api_server.getresponse()
        assert resp.status == 200
        data = json.loads(resp.read())
        assert data["status"] == "ok"


class TestSearchEndpoint:
    def test_search_requires_params(self, api_server):
        """POST /search with missing params returns error."""
        api_server.request(
            "POST", "/search",
            body=json.dumps({"pattern": "foo"}),
            headers={"Content-Type": "application/json"},
        )
        resp = api_server.getresponse()
        data = json.loads(resp.read())
        assert "error" in data

    def test_search_works(self, api_server, tmp_path):
        """POST /search returns results."""
        # Create a file to search
        test_dir = tmp_path / "repo"
        test_dir.mkdir()
        (test_dir / "hello.py").write_text("def hello():\n    return 'world'\n")

        api_server.request(
            "POST", "/search",
            body=json.dumps({"pattern": "hello", "path": str(test_dir)}),
            headers={"Content-Type": "application/json"},
        )
        resp = api_server.getresponse()
        assert resp.status == 200
        data = json.loads(resp.read())
        assert data["match_count"] >= 1
        assert data["backend"] == "ripgrep"


class TestEstimateEndpoint:
    def test_estimate_requires_path(self, api_server):
        """GET /estimate without path returns 400."""
        api_server.request("GET", "/estimate")
        resp = api_server.getresponse()
        assert resp.status == 400

    def test_estimate_works(self, api_server, tmp_path):
        """GET /estimate returns a recommendation."""
        test_dir = tmp_path / "repo"
        test_dir.mkdir()
        (test_dir / "a.py").write_text("hello")

        api_server.request("GET", f"/estimate?path={test_dir}")
        resp = api_server.getresponse()
        assert resp.status == 200
        data = json.loads(resp.read())
        assert "recommendation" in data
        assert "file_count" in data


class TestIndexEndpoint:
    def test_index_status(self, api_server, tmp_path):
        """POST /index with status action works."""
        test_dir = tmp_path / "repo"
        test_dir.mkdir()

        api_server.request(
            "POST", "/index",
            body=json.dumps({"action": "status", "path": str(test_dir)}),
            headers={"Content-Type": "application/json"},
        )
        resp = api_server.getresponse()
        assert resp.status == 200
        data = json.loads(resp.read())
        assert data["indexed"] is False

    def test_index_requires_params(self, api_server):
        """POST /index with missing params returns error."""
        api_server.request(
            "POST", "/index",
            body=json.dumps({"action": "build"}),
            headers={"Content-Type": "application/json"},
        )
        resp = api_server.getresponse()
        data = json.loads(resp.read())
        assert "error" in data


class TestNotFound:
    def test_404(self, api_server):
        """Unknown path returns 404."""
        api_server.request("GET", "/nonexistent")
        resp = api_server.getresponse()
        assert resp.status == 404
