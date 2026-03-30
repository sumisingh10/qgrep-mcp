"""Tests for the PreToolUse Grep intercept hook."""

import json
import os
import subprocess
import sys

import pytest


HOOK_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "hooks", "intercept_grep.py"
)


def run_hook(tool_input: dict, tool_name: str = "Grep", env_override: dict | None = None) -> tuple[int, str, str]:
    """Run the hook script with given input, return (exitcode, stdout, stderr)."""
    payload = json.dumps({
        "session_id": "test-session",
        "tool_name": tool_name,
        "tool_input": tool_input,
    })
    env = os.environ.copy()
    if env_override:
        env.update(env_override)
    proc = subprocess.run(
        [sys.executable, HOOK_SCRIPT],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )
    return proc.returncode, proc.stdout, proc.stderr


class TestInterceptGrep:
    def test_non_grep_tool_allowed(self):
        """Non-Grep tools pass through."""
        rc, _, _ = run_hook({"pattern": "foo", "path": "/tmp"}, tool_name="Read")
        assert rc == 0

    def test_small_directory_allowed(self, tmp_path):
        """Directory with few files passes through."""
        # Create a small directory with a few files
        for i in range(10):
            (tmp_path / f"file{i}.txt").write_text("hello")
        rc, _, _ = run_hook({"pattern": "hello", "path": str(tmp_path)})
        assert rc == 0

    def test_missing_path_allowed(self):
        """Missing or empty path passes through."""
        rc, _, _ = run_hook({"pattern": "foo"})
        assert rc == 0

    def test_nonexistent_path_allowed(self):
        """Non-existent directory passes through."""
        rc, _, _ = run_hook({"pattern": "foo", "path": "/nonexistent/path/xyz"})
        assert rc == 0

    def test_redirect_message_mentions_search_code(self, tmp_path):
        """When redirecting, the message mentions search_code."""
        # We can't easily create 15k+ files in a test, but we can test
        # the redirect message format by checking index-exists path.
        # Create fake index metadata
        import hashlib
        real_path = os.path.realpath(str(tmp_path))
        h = hashlib.sha256(real_path.encode()).hexdigest()[:16]
        cache_dir = tmp_path / "cache" / h
        cache_dir.mkdir(parents=True)
        (cache_dir / "index_meta.json").write_text(json.dumps({
            "repo_path": str(tmp_path),
            "project_name": f"qmcp_{h}",
        }))

        # Also need stats with file_count
        stats_file = tmp_path / "cache" / "stats.json"
        stats_file.write_text(json.dumps({h: {"file_count": 50000}}))

        env = {"QGREP_MCP_CACHE": str(tmp_path / "cache")}
        rc, stdout, stderr = run_hook(
            {"pattern": "foo", "path": str(tmp_path)},
            env_override=env,
        )
        assert rc == 2
        assert "search_code" in stderr
