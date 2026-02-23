import asyncio
import time
from typing import List, Tuple

from fastapi import FastAPI, HTTPException

from utils.engine import (
    THREAD_POOL,
    collect_datastore_paths,
    deduplicate_and_order,
    forge_api_response,
    invoke_search_engine,
    is_record_blacklisted,
    release_event_loop,
    tokenize_output_lines,
)

ULP_BATCH_THRESHOLD: int = 10000
ULP_LARGE_BATCH_SIZE: int = 2500
ULP_SMALL_BATCH_SIZE: int = 1000


def scan_raw_line_batch(raw_batch: List[str]) -> List[str]:
    accepted_lines: List[str] = []
    for raw_line in raw_batch:
        stripped = raw_line.strip()
        if stripped and not is_record_blacklisted(stripped):
            accepted_lines.append(stripped)
    return accepted_lines


async def pipeline_line_extraction(source_lines: List[str]) -> Tuple[List[str], int]:
    line_count = len(source_lines)
    active_batch_size = ULP_LARGE_BATCH_SIZE if line_count > ULP_BATCH_THRESHOLD else ULP_SMALL_BATCH_SIZE
    aggregated_lines: List[str] = []
    active_loop = asyncio.get_running_loop()

    for cursor in range(0, line_count, active_batch_size):
        current_batch = source_lines[cursor: cursor + active_batch_size]
        accepted = await active_loop.run_in_executor(THREAD_POOL, scan_raw_line_batch, current_batch)
        aggregated_lines.extend(accepted)
        await release_event_loop(cursor)

    unique_lines, duplicate_count = await deduplicate_and_order(aggregated_lines)
    return unique_lines, duplicate_count


async def register(application: FastAPI) -> None:
    @application.get("/ulp")
    async def ulp_search_endpoint(site: str):
        if not site.strip():
            raise HTTPException(status_code=400, detail="Parameter 'site' must not be empty")

        request_start = time.perf_counter()
        datastore_paths = collect_datastore_paths(__file__)

        rg_arguments = [
            "rg", "-i", "--no-heading", "--no-line-number",
            "--no-filename", "--fixed-strings", site,
        ] + datastore_paths

        exit_code, stdout_data, stderr_data = await invoke_search_engine(rg_arguments)
        if exit_code not in (0, 1):
            raise HTTPException(status_code=500, detail=f"Search engine error: {stderr_data.strip()}")

        raw_lines = tokenize_output_lines(stdout_data)

        if not raw_lines:
            return forge_api_response(
                {"site": site, "lines": []},
                request_start, 0, 0,
            )

        unique_lines, duplicates_removed = await pipeline_line_extraction(raw_lines)

        return forge_api_response(
            {"site": site, "lines": unique_lines},
            request_start,
            len(unique_lines),
            duplicates_removed,
        )