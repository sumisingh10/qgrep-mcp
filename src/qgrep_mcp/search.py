"""Search orchestrator — routes between ripgrep and qgrep backends."""

import time

from .config import has_qgrep
from .estimator import CostEstimator
from .index import build_index, has_index, is_index_stale, qgrep_search
from .ripgrep import SearchResult, count_files, ripgrep_search


class SearchOrchestrator:
    """Decides which backend to use and executes searches.

    Uses the CostEstimator to pick between ripgrep (linear scan) and qgrep
    (indexed search). Handles auto-rebuilding stale indexes and graceful
    fallback to ripgrep when qgrep fails or is unavailable.
    """

    def __init__(self, estimator: CostEstimator) -> None:
        """Initialize with a CostEstimator for backend selection."""
        self.estimator = estimator

    async def search(
        self,
        pattern: str,
        path: str,
        *,
        glob: str | None = None,
        case_insensitive: bool = False,
        output_mode: str = "content",
        context_lines: int = 0,
        max_results: int = 200,
    ) -> SearchResult:
        """Execute a search, automatically selecting the best backend.

        Falls back to ripgrep when qgrep is unavailable or when features like
        glob filters or context lines are requested (which qgrep doesn't support).
        Auto-rebuilds stale indexes before searching.
        """
        # Ensure we have a file count
        stats = self.estimator._get_stats(path)
        if stats.file_count == 0:
            fc = await count_files(path)
            self.estimator.record_file_count(path, fc)

        # Force ripgrep for features qgrep doesn't support
        force_rg = context_lines > 0 or glob is not None or output_mode != "content"

        if not force_rg:
            # Auto-rebuild stale indexes
            idx_exists = has_index(path)
            if idx_exists and is_index_stale(path):
                try:
                    await build_index(path)
                except RuntimeError:
                    pass  # Will fall through to ripgrep if needed

            rec = self.estimator.estimate(
                path, has_index=has_index(path), has_qgrep=has_qgrep()
            )

            if rec.action == "build_and_use_qgrep":
                try:
                    meta = await build_index(path)
                    self.estimator.record_build_time(path, meta.build_time_seconds)
                    return await self._search_qgrep(
                        pattern, path,
                        case_insensitive=case_insensitive,
                        max_results=max_results,
                    )
                except RuntimeError:
                    # Fall through to ripgrep
                    pass

            elif rec.action == "use_qgrep":
                try:
                    return await self._search_qgrep(
                        pattern, path,
                        case_insensitive=case_insensitive,
                        max_results=max_results,
                    )
                except RuntimeError:
                    # Fall through to ripgrep
                    pass

        # Default: ripgrep
        return await self._search_rg(
            pattern, path,
            glob=glob,
            case_insensitive=case_insensitive,
            output_mode=output_mode,
            context_lines=context_lines,
            max_results=max_results,
        )

    async def _search_rg(
        self,
        pattern: str,
        path: str,
        **kwargs,
    ) -> SearchResult:
        """Run a ripgrep search and record the latency."""
        result = await ripgrep_search(pattern, path, **kwargs)
        self.estimator.record_rg(path, result.elapsed_seconds)
        return result

    async def _search_qgrep(
        self,
        pattern: str,
        path: str,
        *,
        case_insensitive: bool = False,
        max_results: int = 200,
    ) -> SearchResult:
        """Run a qgrep indexed search and record the latency."""
        start = time.monotonic()
        lines = await qgrep_search(
            pattern, path,
            case_insensitive=case_insensitive,
            max_results=max_results,
        )
        elapsed = time.monotonic() - start
        self.estimator.record_qgrep(path, elapsed)

        files_seen: set[str] = set()
        for line in lines:
            if ":" in line:
                files_seen.add(line.split(":")[0])

        return SearchResult(
            matches=lines,
            file_count=len(files_seen),
            match_count=len(lines),
            backend="qgrep",
            elapsed_seconds=round(elapsed, 4),
            truncated=len(lines) >= max_results,
        )
