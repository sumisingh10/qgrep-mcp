"""Tests for index metadata management, staleness detection, and pre-session warming."""

import json
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

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


class TestLoadFromFile:
    """Tests for IndexMetadata.load_from_file."""

    def test_load_from_file(self, tmp_cache):
        """load_from_file loads metadata from an explicit file path."""
        meta = IndexMetadata(
            repo_path="/test/repo",
            project_name="qmcp_test",
            created_at=1000.0,
            build_time_seconds=2.0,
        )
        meta.save("/test/repo")
        from qgrep_mcp.config import repo_cache_dir
        meta_file = repo_cache_dir("/test/repo") / "index_meta.json"
        loaded = IndexMetadata.load_from_file(meta_file)
        assert loaded is not None
        assert loaded.project_name == "qmcp_test"

    def test_load_from_file_missing(self, tmp_cache):
        """load_from_file returns None for a missing file."""
        loaded = IndexMetadata.load_from_file(Path("/nonexistent/meta.json"))
        assert loaded is None


class TestPreSessionWarming:
    """Tests for the warm_stale_indexes startup routine."""

    @pytest.mark.asyncio
    async def test_warm_rebuilds_stale(self, tmp_path, tmp_cache):
        """warm_stale_indexes rebuilds a stale index."""
        from qgrep_mcp.server import warm_stale_indexes

        # Create a repo with files
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "a.py").write_text("hello")

        # Save a stale index (created_at in the past)
        meta = IndexMetadata(
            repo_path=str(repo),
            project_name="test",
            created_at=time.time() - 100,
            build_time_seconds=1.0,
        )
        meta.save(str(repo))

        # Touch a file to make it stale
        time.sleep(0.05)
        (repo / "a.py").write_text("modified")

        # Mock build_index to avoid needing qgrep
        with patch("qgrep_mcp.server.build_index", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = IndexMetadata(
                repo_path=str(repo),
                project_name="test",
                created_at=time.time(),
                build_time_seconds=0.5,
            )
            await warm_stale_indexes()
            mock_build.assert_called_once_with(str(repo))

    @pytest.mark.asyncio
    async def test_warm_skips_fresh(self, tmp_path, tmp_cache):
        """warm_stale_indexes does not rebuild a fresh index."""
        from qgrep_mcp.server import warm_stale_indexes

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

        with patch("qgrep_mcp.server.build_index", new_callable=AsyncMock) as mock_build:
            await warm_stale_indexes()
            mock_build.assert_not_called()

    @pytest.mark.asyncio
    async def test_warm_skips_missing_repo(self, tmp_cache):
        """warm_stale_indexes skips repos whose paths no longer exist."""
        from qgrep_mcp.server import warm_stale_indexes

        meta = IndexMetadata(
            repo_path="/nonexistent/repo/path",
            project_name="test",
            created_at=time.time() - 100,
            build_time_seconds=1.0,
        )
        meta.save("/nonexistent/repo/path")

        with patch("qgrep_mcp.server.build_index", new_callable=AsyncMock) as mock_build:
            await warm_stale_indexes()
            mock_build.assert_not_called()
