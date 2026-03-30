"""qgrep index lifecycle management."""

import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass

from .config import find_binary, qgrep_project_name, repo_cache_dir


@dataclass
class IndexMetadata:
    repo_path: str
    project_name: str
    created_at: float = 0.0
    build_time_seconds: float = 0.0
    file_count: int = 0

    def save(self, path: str) -> None:
        meta_file = repo_cache_dir(path) / "index_meta.json"
        meta_file.write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def load(cls, path: str) -> "IndexMetadata | None":
        meta_file = repo_cache_dir(path) / "index_meta.json"
        if not meta_file.exists():
            return None
        try:
            data = json.loads(meta_file.read_text())
            return cls(**data)
        except (json.JSONDecodeError, TypeError):
            return None


def has_index(path: str) -> bool:
    """Check if an index exists for the given path."""
    return IndexMetadata.load(path) is not None


async def _run_qgrep(*args: str) -> tuple[int, str, str]:
    """Run a qgrep command and return (returncode, stdout, stderr)."""
    qgrep = find_binary("qgrep")
    if not qgrep:
        raise RuntimeError("qgrep is not installed")
    proc = await asyncio.create_subprocess_exec(
        qgrep, *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return (
        proc.returncode or 0,
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
    )


async def build_index(path: str) -> IndexMetadata:
    """Build a qgrep index: init project then build."""
    project = qgrep_project_name(path)

    # Init the project (idempotent — overwrites if exists)
    rc, out, err = await _run_qgrep("init", project, path)
    if rc != 0:
        raise RuntimeError(f"qgrep init failed (rc={rc}): {err.strip()}")

    # Build the index
    start = time.monotonic()
    rc, out, err = await _run_qgrep("build", project)
    build_time = time.monotonic() - start

    if rc != 0:
        raise RuntimeError(f"qgrep build failed (rc={rc}): {err.strip()}")

    meta = IndexMetadata(
        repo_path=path,
        project_name=project,
        created_at=time.time(),
        build_time_seconds=round(build_time, 2),
    )
    meta.save(path)
    return meta


async def delete_index(path: str) -> bool:
    """Delete a qgrep index and its metadata."""
    meta = IndexMetadata.load(path)
    if meta is None:
        return False

    # Remove our metadata file
    meta_file = repo_cache_dir(path) / "index_meta.json"
    meta_file.unlink(missing_ok=True)

    # Remove the qgrep project config (~/.qgrep/<project>.cfg and database)
    qgrep_dir = os.path.expanduser("~/.qgrep")
    for ext in (".cfg", ".qgd", ".qgf"):
        f = os.path.join(qgrep_dir, meta.project_name + ext)
        if os.path.exists(f):
            os.unlink(f)

    return True


async def index_status(path: str) -> dict:
    """Get index status for a path."""
    meta = IndexMetadata.load(path)
    if meta is None:
        return {"indexed": False, "path": path}

    # Also check if qgrep actually has the project
    try:
        rc, out, err = await _run_qgrep("info", meta.project_name)
        qgrep_info = out.strip() if rc == 0 else None
    except RuntimeError:
        qgrep_info = None

    return {
        "indexed": True,
        "path": path,
        "project_name": meta.project_name,
        "created_at": meta.created_at,
        "build_time_seconds": meta.build_time_seconds,
        "qgrep_info": qgrep_info,
    }


async def qgrep_search(
    pattern: str,
    path: str,
    *,
    case_insensitive: bool = False,
    max_results: int = 200,
) -> list[str]:
    """Run qgrep search and return raw output lines."""
    meta = IndexMetadata.load(path)
    if meta is None:
        raise RuntimeError("No index exists for this path")

    # qgrep search options are positional flags before the query:
    #   i = case-insensitive, L<n> = limit lines
    opts = f"L{max_results}"
    if case_insensitive:
        opts = "i" + opts

    rc, out, err = await _run_qgrep("search", meta.project_name, opts, pattern)

    if rc not in (0, 1):
        raise RuntimeError(f"qgrep search failed: {err.strip()}")

    lines = out.splitlines()
    return lines[:max_results]
