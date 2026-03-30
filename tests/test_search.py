"""Tests for the search orchestrator."""

from unittest.mock import AsyncMock, patch

import pytest

from qgrep_mcp import config
from qgrep_mcp.estimator import CostEstimator
from qgrep_mcp.ripgrep import SearchResult
from qgrep_mcp.search import SearchOrchestrator


@pytest.fixture(autouse=True)
def tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(config, "STATS_FILE", tmp_path / "stats.json")
    return tmp_path


@pytest.fixture
def estimator():
    return CostEstimator()


@pytest.fixture
def orchestrator(estimator):
    return SearchOrchestrator(estimator)


class TestSearchOrchestrator:
    @pytest.mark.asyncio
    async def test_glob_forces_ripgrep(self, orchestrator):
        """When glob is set, always use ripgrep."""
        mock_result = SearchResult(
            matches=["file.py:1:hello"],
            file_count=1,
            match_count=1,
            backend="ripgrep",
            elapsed_seconds=0.1,
        )
        with patch("qgrep_mcp.search.ripgrep_search", new_callable=AsyncMock, return_value=mock_result):
            with patch("qgrep_mcp.search.count_files", new_callable=AsyncMock, return_value=100):
                result = await orchestrator.search("hello", "/tmp", glob="*.py")
        assert result.backend == "ripgrep"

    @pytest.mark.asyncio
    async def test_context_lines_forces_ripgrep(self, orchestrator):
        """When context_lines > 0, always use ripgrep."""
        mock_result = SearchResult(
            matches=["file.py:1:hello"],
            file_count=1,
            match_count=1,
            backend="ripgrep",
            elapsed_seconds=0.1,
        )
        with patch("qgrep_mcp.search.ripgrep_search", new_callable=AsyncMock, return_value=mock_result):
            with patch("qgrep_mcp.search.count_files", new_callable=AsyncMock, return_value=100):
                result = await orchestrator.search("hello", "/tmp", context_lines=3)
        assert result.backend == "ripgrep"

    @pytest.mark.asyncio
    async def test_default_uses_ripgrep(self, orchestrator):
        """Default (no index, few searches) uses ripgrep."""
        mock_result = SearchResult(
            matches=["file.py:1:hello"],
            file_count=1,
            match_count=1,
            backend="ripgrep",
            elapsed_seconds=0.1,
        )
        with patch("qgrep_mcp.search.ripgrep_search", new_callable=AsyncMock, return_value=mock_result):
            with patch("qgrep_mcp.search.count_files", new_callable=AsyncMock, return_value=100):
                with patch("qgrep_mcp.search.has_qgrep", return_value=False):
                    result = await orchestrator.search("hello", "/tmp")
        assert result.backend == "ripgrep"

    @pytest.mark.asyncio
    async def test_qgrep_fallback_on_error(self, orchestrator, estimator):
        """If qgrep search fails, fall back to ripgrep."""
        estimator.record_file_count("/repo", 5000)
        for _ in range(3):
            estimator.record_rg("/repo", 1.0)

        mock_rg = SearchResult(
            matches=["file.py:1:test"],
            file_count=1,
            match_count=1,
            backend="ripgrep",
            elapsed_seconds=0.1,
        )

        with patch("qgrep_mcp.search.has_index", return_value=True):
            with patch("qgrep_mcp.search.has_qgrep", return_value=True):
                with patch("qgrep_mcp.search.qgrep_search", new_callable=AsyncMock, side_effect=RuntimeError("fail")):
                    with patch("qgrep_mcp.search.ripgrep_search", new_callable=AsyncMock, return_value=mock_rg):
                        with patch("qgrep_mcp.search.count_files", new_callable=AsyncMock, return_value=5000):
                            result = await orchestrator.search("test", "/repo")
        assert result.backend == "ripgrep"
