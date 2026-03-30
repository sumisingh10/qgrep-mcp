"""Amortized cost estimator — decides whether indexing is worth it at query time."""

import json
import os
import time
from dataclasses import dataclass, field
from . import config
from .config import repo_hash


@dataclass
class RepoStats:
    rg_latencies: list[float] = field(default_factory=list)
    qgrep_latencies: list[float] = field(default_factory=list)
    file_count: int = 0
    index_build_time: float | None = None
    last_updated: float = 0.0

    def avg_rg(self) -> float:
        if not self.rg_latencies:
            return 0.0
        return sum(self.rg_latencies) / len(self.rg_latencies)

    def avg_qgrep(self) -> float:
        if not self.qgrep_latencies:
            return 0.0
        return sum(self.qgrep_latencies) / len(self.qgrep_latencies)


@dataclass
class Recommendation:
    action: str  # "use_ripgrep" | "use_qgrep" | "build_and_use_qgrep"
    confidence: str  # "high" | "medium" | "low"
    reasoning: str
    stats: dict


class CostEstimator:
    """Tracks search stats and recommends backend."""

    def __init__(self) -> None:
        self._all_stats: dict[str, RepoStats] = {}
        self._session_searches: dict[str, int] = {}
        self._load()

    # --- Persistence ---

    def _load(self) -> None:
        if config.STATS_FILE.exists():
            try:
                raw = json.loads(config.STATS_FILE.read_text())
                for key, val in raw.items():
                    self._all_stats[key] = RepoStats(
                        rg_latencies=val.get("rg_latencies", []),
                        qgrep_latencies=val.get("qgrep_latencies", []),
                        file_count=val.get("file_count", 0),
                        index_build_time=val.get("index_build_time"),
                        last_updated=val.get("last_updated", 0.0),
                    )
            except (json.JSONDecodeError, KeyError):
                self._all_stats = {}

    def _save(self) -> None:
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        data = {}
        for key, st in self._all_stats.items():
            data[key] = {
                "rg_latencies": st.rg_latencies,
                "qgrep_latencies": st.qgrep_latencies,
                "file_count": st.file_count,
                "index_build_time": st.index_build_time,
                "last_updated": st.last_updated,
            }
        tmp = config.STATS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, config.STATS_FILE)

    # --- Recording ---

    def _get_stats(self, path: str) -> RepoStats:
        h = repo_hash(path)
        if h not in self._all_stats:
            self._all_stats[h] = RepoStats()
        return self._all_stats[h]

    def record_rg(self, path: str, latency: float) -> None:
        st = self._get_stats(path)
        st.rg_latencies.append(latency)
        st.rg_latencies = st.rg_latencies[-config.LATENCY_WINDOW:]
        st.last_updated = time.time()
        self._bump_session(path)
        self._save()

    def record_qgrep(self, path: str, latency: float) -> None:
        st = self._get_stats(path)
        st.qgrep_latencies.append(latency)
        st.qgrep_latencies = st.qgrep_latencies[-config.LATENCY_WINDOW:]
        st.last_updated = time.time()
        self._bump_session(path)
        self._save()

    def record_file_count(self, path: str, count: int) -> None:
        st = self._get_stats(path)
        st.file_count = count
        st.last_updated = time.time()
        self._save()

    def record_build_time(self, path: str, build_time: float) -> None:
        st = self._get_stats(path)
        st.index_build_time = build_time
        st.last_updated = time.time()
        self._save()

    def _bump_session(self, path: str) -> None:
        h = repo_hash(path)
        self._session_searches[h] = self._session_searches.get(h, 0) + 1

    def session_searches(self, path: str) -> int:
        return self._session_searches.get(repo_hash(path), 0)

    # --- Estimation ---

    def estimate(self, path: str, *, has_index: bool, has_qgrep: bool) -> Recommendation:
        st = self._get_stats(path)
        ss = self.session_searches(path)
        fc = st.file_count
        avg_rg = st.avg_rg()
        avg_qg = st.avg_qgrep()

        stats_dict = {
            "file_count": fc,
            "session_searches": ss,
            "avg_rg_latency": round(avg_rg, 4),
            "avg_qgrep_latency": round(avg_qg, 4),
            "index_build_time": st.index_build_time,
            "has_index": has_index,
            "has_qgrep": has_qgrep,
            "total_rg_samples": len(st.rg_latencies),
            "total_qgrep_samples": len(st.qgrep_latencies),
        }

        # No qgrep binary → always ripgrep
        if not has_qgrep:
            return Recommendation(
                action="use_ripgrep",
                confidence="high",
                reasoning="qgrep is not installed. Install it for indexed search on large repos.",
                stats=stats_dict,
            )

        # Small repos → always ripgrep
        if fc > 0 and fc < config.SMALL_REPO_THRESHOLD:
            return Recommendation(
                action="use_ripgrep",
                confidence="high",
                reasoning=f"Small repo ({fc} files) — ripgrep is fast enough.",
                stats=stats_dict,
            )

        # Index exists → use it (skip cold start — index is already built)
        if has_index and st.index_build_time is not None and avg_rg > avg_qg > 0:
            speedup = avg_rg - avg_qg
            breakeven = st.index_build_time / speedup if speedup > 0 else float("inf")
            if ss >= breakeven:
                return Recommendation(
                    action="use_qgrep",
                    confidence="high",
                    reasoning=f"Index exists and past breakeven ({ss} searches, breakeven ~{breakeven:.0f}).",
                    stats=stats_dict,
                )
            else:
                return Recommendation(
                    action="use_qgrep",
                    confidence="medium",
                    reasoning=f"Index exists. Approaching breakeven ({ss}/{breakeven:.0f} searches).",
                    stats=stats_dict,
                )

        if has_index:
            return Recommendation(
                action="use_qgrep",
                confidence="medium",
                reasoning="Index exists — using it.",
                stats=stats_dict,
            )

        # Large repos → build immediately, no cold start needed
        # (file count alone is a strong enough signal, r=0.96 with rg latency)
        if fc >= config.LARGE_REPO_THRESHOLD:
            return Recommendation(
                action="build_and_use_qgrep",
                confidence="high",
                reasoning=f"Large repo ({fc} files) — indexing will be much faster than ripgrep.",
                stats=stats_dict,
            )

        # Gray zone (5k-15k): cold start to gather latency baselines
        total_samples = len(st.rg_latencies)
        if total_samples < config.COLD_START_SEARCHES:
            return Recommendation(
                action="use_ripgrep",
                confidence="low",
                reasoning=f"Cold start — collecting latency baselines ({total_samples}/{config.COLD_START_SEARCHES}).",
                stats=stats_dict,
            )

        if avg_rg > config.RG_SLOW_THRESHOLD and ss >= config.COLD_START_SEARCHES:
            return Recommendation(
                action="build_and_use_qgrep",
                confidence="medium",
                reasoning=f"Ripgrep averaging {avg_rg:.2f}s (>{config.RG_SLOW_THRESHOLD}s) with {ss} searches — indexing recommended.",
                stats=stats_dict,
            )

        # Default: use ripgrep
        return Recommendation(
            action="use_ripgrep",
            confidence="medium",
            reasoning="Not enough data or searches to justify indexing yet.",
            stats=stats_dict,
        )
