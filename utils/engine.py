import asyncio
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Tuple

from fastapi import HTTPException

API_OWNER: str = "@ISmartCoder"
API_DEV: str = "@abirxdhackz"
API_VERSION: str = "2.3.68"

THREAD_POOL: ThreadPoolExecutor = ThreadPoolExecutor(
    max_workers=max(8, (os.cpu_count() or 4) * 2)
)

BLACKLIST_SIGNATURES: List[str] = [
    "file:///",
    "google.com/maps",
    "search?",
    "mailto:",
    "javascript:",
    "data:",
    "chrome://",
    "about:",
    "moz-extension://",
    "chrome-extension://",
    "[not_saved]",
]

_BLACKLIST_FROZEN: FrozenSet[str] = frozenset(BLACKLIST_SIGNATURES)

RG_BINARY: str = "rg"
RG_TIMEOUT_SECONDS: int = 300


def resolve_host_address() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe_socket:
            probe_socket.connect(("8.8.8.8", 80))
            return probe_socket.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def is_record_blacklisted(raw_line: str) -> bool:
    normalised = raw_line.lower()
    for signature in _BLACKLIST_FROZEN:
        if signature in normalised:
            return True
    return False


def collect_datastore_paths(caller_file: str) -> List[str]:
    datastore_dir = Path(caller_file).resolve().parent.parent / "data"
    if not datastore_dir.exists() or not datastore_dir.is_dir():
        raise HTTPException(status_code=404, detail="Datastore directory not found")
    discovered = [str(entry) for entry in datastore_dir.glob("*.txt") if entry.is_file()]
    if not discovered:
        raise HTTPException(status_code=404, detail="No .txt files found in datastore directory")
    return discovered


def forge_api_response(
    payload: dict,
    request_start: float,
    total_lines: int,
    duplicates_removed: int,
) -> dict:
    elapsed_ms = round((time.perf_counter() - request_start) * 1000, 2)
    return {
        **payload,
        "api_owner": API_OWNER,
        "api_dev": API_DEV,
        "api_version": API_VERSION,
        "time_taken": f"{elapsed_ms}ms",
        "total_lines": total_lines,
        "duplicates_removed": duplicates_removed,
    }


async def invoke_search_engine(
    rg_arguments: List[str],
    timeout_seconds: int = RG_TIMEOUT_SECONDS,
) -> Tuple[int, str, str]:
    try:
        search_process = await asyncio.create_subprocess_exec(
            *rg_arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            raw_stdout, raw_stderr = await asyncio.wait_for(
                search_process.communicate(),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            search_process.kill()
            await search_process.communicate()
            return -1, "", f"ripgrep timed out after {timeout_seconds}s"
        return (
            search_process.returncode,
            raw_stdout.decode("utf-8", errors="replace"),
            raw_stderr.decode("utf-8", errors="replace"),
        )
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="ripgrep binary not found — install ripgrep on the server")
    except Exception as runtime_error:
        raise HTTPException(status_code=500, detail=f"Search engine invocation failed: {runtime_error}")


def tokenize_output_lines(raw_stdout: str) -> List[str]:
    if not raw_stdout:
        return []
    return [segment for segment in raw_stdout.split("\n") if segment.strip()]


async def deduplicate_and_order(
    source_items: List[str],
) -> Tuple[List[str], int]:
    original_count = len(source_items)

    def _dedup_worker(records: List[str]) -> List[str]:
        observed: set = set()
        unique_records: List[str] = []
        for record in records:
            fingerprint = record.lower()
            if fingerprint not in observed:
                observed.add(fingerprint)
                unique_records.append(record)
        unique_records.sort()
        return unique_records

    active_loop = asyncio.get_running_loop()
    deduplicated = await active_loop.run_in_executor(THREAD_POOL, _dedup_worker, source_items)
    removed_count = original_count - len(deduplicated)
    return deduplicated, removed_count


async def release_event_loop(iteration_index: int, release_interval: int = 2500) -> None:
    if iteration_index > 0 and iteration_index % release_interval == 0:
        await asyncio.sleep(0)