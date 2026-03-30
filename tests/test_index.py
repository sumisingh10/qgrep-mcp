"""Tests for index metadata management."""

import json

import pytest

from qgrep_mcp import config
from qgrep_mcp.index import IndexMetadata, has_index


@pytest.fixture(autouse=True)
def tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    return tmp_path


class TestIndexMetadata:
    def test_save_and_load(self):
        meta = IndexMetadata(
            repo_path="/test/repo",
            project_name="qmcp_abc123",
            created_at=1000.0,
            build_time_seconds=5.5,
            file_count=1234,
        )
        meta.save("/test/repo")

        loaded = IndexMetadata.load("/test/repo")
        assert loaded is not None
        assert loaded.repo_path == "/test/repo"
        assert loaded.project_name == "qmcp_abc123"
        assert loaded.build_time_seconds == 5.5

    def test_load_missing(self):
        loaded = IndexMetadata.load("/nonexistent/path")
        assert loaded is None

    def test_has_index_false(self):
        assert has_index("/nonexistent") is False

    def test_has_index_true(self):
        meta = IndexMetadata(
            repo_path="/test/repo",
            project_name="qmcp_abc123",
        )
        meta.save("/test/repo")
        assert has_index("/test/repo") is True

    def test_load_corrupted_json(self, tmp_cache):
        """Corrupted metadata returns None."""
        from qgrep_mcp.config import repo_cache_dir
        d = repo_cache_dir("/test/repo")
        (d / "index_meta.json").write_text("not json")
        assert IndexMetadata.load("/test/repo") is None
