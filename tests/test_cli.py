"""Tests for the CLI interface."""

from unittest.mock import AsyncMock, patch

import pytest

from qgrep_mcp import config
from qgrep_mcp.cli import build_parser, main
from qgrep_mcp.ripgrep import SearchResult


@pytest.fixture(autouse=True)
def tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(config, "STATS_FILE", tmp_path / "stats.json")
    return tmp_path


class TestParser:
    """Test argument parsing."""

    def test_search_basic(self):
        parser = build_parser()
        args = parser.parse_args(["search", "TODO"])
        assert args.command == "search"
        assert args.pattern == "TODO"
        assert args.path is None

    def test_search_with_path(self):
        parser = build_parser()
        args = parser.parse_args(["search", "TODO", "/tmp"])
        assert args.path == "/tmp"

    def test_search_flags(self):
        parser = build_parser()
        args = parser.parse_args(["search", "-i", "-g", "*.py", "-C", "3", "-m", "50", "pattern", "."])
        assert args.ignore_case is True
        assert args.glob == "*.py"
        assert args.context == 3
        assert args.max_results == 50

    def test_search_files_only(self):
        parser = build_parser()
        args = parser.parse_args(["search", "-l", "pattern"])
        assert args.output_mode == "files_with_matches"

    def test_index_build(self):
        parser = build_parser()
        args = parser.parse_args(["index", "build", "/tmp/repo"])
        assert args.command == "index"
        assert args.action == "build"
        assert args.path == "/tmp/repo"

    def test_estimate(self):
        parser = build_parser()
        args = parser.parse_args(["estimate", "--json", "."])
        assert args.command == "estimate"
        assert args.json is True

    def test_serve_http(self):
        parser = build_parser()
        args = parser.parse_args(["serve", "--http", "--port", "9000"])
        assert args.http is True
        assert args.port == 9000

    def test_no_command_prints_help(self, capsys, monkeypatch):
        monkeypatch.setattr("sys.argv", ["qgrep-mcp"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0


class TestSearchCommand:
    """Test the search subcommand end-to-end."""

    @patch("qgrep_mcp.cli.SearchOrchestrator")
    @patch("qgrep_mcp.cli.CostEstimator")
    def test_search_prints_matches(self, mock_est, mock_orch_cls, capsys, monkeypatch):
        mock_orch = mock_orch_cls.return_value
        mock_orch.search = AsyncMock(return_value=SearchResult(
            matches=["file.py:1:hello world", "file.py:5:hello again"],
            file_count=1,
            match_count=2,
            backend="ripgrep",
            elapsed_seconds=0.05,
        ))

        monkeypatch.setattr("sys.argv", ["qgrep-mcp", "search", "hello", "/tmp"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0

        captured = capsys.readouterr()
        assert "file.py:1:hello world" in captured.out
        assert "file.py:5:hello again" in captured.out

    @patch("qgrep_mcp.cli.SearchOrchestrator")
    @patch("qgrep_mcp.cli.CostEstimator")
    def test_search_error_returns_1(self, mock_est, mock_orch_cls, capsys, monkeypatch):
        mock_orch = mock_orch_cls.return_value
        mock_orch.search = AsyncMock(return_value=SearchResult(
            error="rg not found",
        ))

        monkeypatch.setattr("sys.argv", ["qgrep-mcp", "search", "pattern", "/tmp"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1

        captured = capsys.readouterr()
        assert "rg not found" in captured.err

    @patch("qgrep_mcp.cli.SearchOrchestrator")
    @patch("qgrep_mcp.cli.CostEstimator")
    def test_search_stats_flag(self, mock_est, mock_orch_cls, capsys, monkeypatch):
        mock_orch = mock_orch_cls.return_value
        mock_orch.search = AsyncMock(return_value=SearchResult(
            matches=["a:1:x"],
            file_count=1,
            match_count=1,
            backend="qgrep",
            elapsed_seconds=0.01,
        ))

        monkeypatch.setattr("sys.argv", ["qgrep-mcp", "search", "--stats", "x", "/tmp"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0

        captured = capsys.readouterr()
        assert "qgrep" in captured.err
        assert "0.01s" in captured.err


class TestEstimateCommand:
    """Test the estimate subcommand."""

    @patch("qgrep_mcp.ripgrep.count_files", new_callable=AsyncMock, return_value=500)
    @patch("qgrep_mcp.index.has_index", return_value=False)
    @patch("qgrep_mcp.config.has_qgrep", return_value=True)
    def test_estimate_text(self, _hq, _hi, _cf, capsys, monkeypatch):
        monkeypatch.setattr("sys.argv", ["qgrep-mcp", "estimate", "/tmp"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0

        captured = capsys.readouterr()
        assert "Files:" in captured.out
        assert "Recommendation:" in captured.out
