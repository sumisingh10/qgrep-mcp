"""Tests for index metadata management."""

import json
import os
import time

import pytest

from qgrep_mcp import config
from qgrep_mcp.index import IndexMetadata, has_index, is_index_stale


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


class TestStaleIndex:
    def test_no_index_not_stale(self):
        """No index means not stale."""
        assert is_index_stale("/nonexistent") is False

    def test_fresh_index_not_stale(self, tmp_path, tmp_cache):
        """Index built after all files were written is not stale."""
        # Create some files
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "a.py").write_text("hello")
        (repo / "b.py").write_text("world")
        time.sleep(0.05)  # Ensure index timestamp is after file writes

        # Save index metadata with current time
        meta = IndexMetadata(
            repo_path=str(repo),
            project_name="test",
            created_at=time.time(),
            build_time_seconds=1.0,
        )
        meta.save(str(repo))

        assert is_index_stale(str(repo)) is False

    def test_stale_index_detected(self, tmp_path, tmp_cache):
        """Index built before a file was modified is stale."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "a.py").write_text("hello")

        # Save index metadata with past timestamp
        meta = IndexMetadata(
            repo_path=str(repo),
            project_name="test",
            created_at=time.time() - 10,
            build_time_seconds=1.0,
        )
        meta.save(str(repo))

        # Modify a file after the index was "built"
        time.sleep(0.05)
        (repo / "a.py").write_text("modified")

        assert is_index_stale(str(repo)) is True

    def test_git_dir_skipped(self, tmp_path, tmp_cache):
        """Files inside .git should not trigger staleness."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "a.py").write_text("hello")
        time.sleep(0.05)

        meta = IndexMetadata(
            repo_path=str(repo),
            project_name="test",
            created_at=time.time(),
            build_time_seconds=1.0,
        )
        meta.save(str(repo))

        # Write a file inside .git after index
        git_dir = repo / ".git"
        git_dir.mkdir()
        time.sleep(0.05)
        (git_dir / "HEAD").write_text("ref: refs/heads/main")

        assert is_index_stale(str(repo)) is False
