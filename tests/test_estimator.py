"""Tests for the amortized cost estimator."""

import pytest

from qgrep_mcp import config
from qgrep_mcp.estimator import CostEstimator, RepoStats


@pytest.fixture(autouse=True)
def tmp_cache(tmp_path, monkeypatch):
    """Redirect cache to a temp directory."""
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(config, "STATS_FILE", tmp_path / "stats.json")
    return tmp_path


class TestRepoStats:
    def test_avg_empty(self):
        st = RepoStats()
        assert st.avg_rg() == 0.0
        assert st.avg_qgrep() == 0.0

    def test_avg_values(self):
        st = RepoStats(rg_latencies=[1.0, 2.0, 3.0])
        assert st.avg_rg() == 2.0


class TestCostEstimator:
    def test_no_qgrep_always_ripgrep(self):
        est = CostEstimator()
        rec = est.estimate("/some/path", has_index=False, has_qgrep=False)
        assert rec.action == "use_ripgrep"
        assert rec.confidence == "high"

    def test_small_repo_always_ripgrep(self):
        est = CostEstimator()
        est.record_file_count("/small", 2000)
        # Pass cold start (now 2)
        for _ in range(2):
            est.record_rg("/small", 0.1)
        rec = est.estimate("/small", has_index=False, has_qgrep=True)
        assert rec.action == "use_ripgrep"
        assert rec.confidence == "high"

    def test_cold_start(self):
        est = CostEstimator()
        est.record_file_count("/cold-start-repo", 8000)
        est.record_rg("/cold-start-repo", 0.5)
        rec = est.estimate("/cold-start-repo", has_index=False, has_qgrep=True)
        assert rec.action == "use_ripgrep"
        assert rec.confidence == "low"
        assert "Cold start" in rec.reasoning

    def test_large_repo_triggers_build(self):
        est = CostEstimator()
        est.record_file_count("/big", 20000)
        # Pass cold start
        for _ in range(2):
            est.record_rg("/big", 5.0)
        rec = est.estimate("/big", has_index=False, has_qgrep=True)
        assert rec.action == "build_and_use_qgrep"
        assert rec.confidence == "high"

    def test_existing_index_used(self):
        est = CostEstimator()
        est.record_file_count("/indexed", 8000)
        for _ in range(2):
            est.record_rg("/indexed", 1.0)
        rec = est.estimate("/indexed", has_index=True, has_qgrep=True)
        assert rec.action == "use_qgrep"

    def test_slow_rg_triggers_build_in_gray_zone(self):
        """In 5k-15k range, slow rg (>1s) after cold start triggers build."""
        est = CostEstimator()
        est.record_file_count("/slow", 8000)
        for _ in range(3):
            est.record_rg("/slow", 2.0)
        rec = est.estimate("/slow", has_index=False, has_qgrep=True)
        assert rec.action == "build_and_use_qgrep"

    def test_fast_rg_in_gray_zone_stays_ripgrep(self):
        """In 5k-15k range, fast rg stays on ripgrep."""
        est = CostEstimator()
        est.record_file_count("/grayfast", 8000)
        for _ in range(3):
            est.record_rg("/grayfast", 0.3)
        rec = est.estimate("/grayfast", has_index=False, has_qgrep=True)
        assert rec.action == "use_ripgrep"

    def test_persistence_roundtrip(self, tmp_cache):
        est = CostEstimator()
        est.record_file_count("/persist-test", 8000)
        est.record_rg("/persist-test", 1.5)

        # New estimator loads from disk
        est2 = CostEstimator()
        st = est2._get_stats("/persist-test")
        assert st.file_count == 8000
        assert len(st.rg_latencies) == 1

    def test_latency_window_capped(self):
        est = CostEstimator()
        for i in range(30):
            est.record_rg("/capped", float(i))
        st = est._get_stats("/capped")
        assert len(st.rg_latencies) == 20

    def test_breakeven_calculation(self):
        est = CostEstimator()
        est.record_file_count("/be", 10000)
        est.record_build_time("/be", 30.0)
        for _ in range(3):
            est.record_rg("/be", 2.0)
            est.record_qgrep("/be", 0.5)
        rec = est.estimate("/be", has_index=True, has_qgrep=True)
        assert rec.action == "use_qgrep"
        assert "breakeven" in rec.reasoning.lower() or "Approaching" in rec.reasoning
