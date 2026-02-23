import asyncio
import re
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

RX_COMBO_DETECTOR = re.compile(
    r'\b\S+\s*[:;|,]\s*\S+',
    re.IGNORECASE,
)

RX_LOGIN_VALIDATOR = re.compile(
    r'^(?:[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}|\S+)$'
)

URL_SIGNATURE_HINTS: Tuple[str, ...] = (
    '://', 'http', 'android://', 'ftp', '.com', '.org', '.net', 'www.'
)

INVALID_USERNAME_MARKERS: Tuple[str, ...] = (
    'http', 'www.', 'file:///', 'android://', 'ftp://', 'vgecah', 'warning', 'stopped', 'found'
)

INVALID_PATH_MARKERS: Tuple[str, ...] = (
    'auth', 'login', 'register', 'checkout', 'app', 'classroom', 'store', 'affiliation'
)

BLOCKED_TLD_SET: frozenset = frozenset({
    'com', 'org', 'net', 'edu', 'gov', 'io', 'co'
})

COMBO_BATCH_THRESHOLD: int = 10000
COMBO_LARGE_BATCH_SIZE: int = 2500
COMBO_SMALL_BATCH_SIZE: int = 1000
COMBO_DELIMITER_PRIORITY: Tuple[str, ...] = (':', '|', ';', ',')
COMBO_SPLIT_DELIMITER_PATTERN = re.compile(r'[:|]')
MIN_FIELD_LENGTH: int = 3


def scan_combo_batch(raw_batch: List[str]) -> Tuple[List[str], int]:
    extracted_combos: List[str] = []
    rejected_count: int = 0

    for raw_line in raw_batch:
        stripped = raw_line.strip()
        if not stripped:
            rejected_count += 1
            continue
        if is_record_blacklisted(stripped):
            rejected_count += 1
            continue

        split_segments = [seg for seg in COMBO_SPLIT_DELIMITER_PATTERN.split(stripped) if seg]
        if len(split_segments) < 2:
            rejected_count += 1
            continue

        if any(hint in stripped.lower()[:30] for hint in URL_SIGNATURE_HINTS) or stripped.count(':') >= 2:
            username_field = split_segments[-2].strip()
            password_field = split_segments[-1].strip()
        else:
            detected_combos = RX_COMBO_DETECTOR.findall(stripped)
            if not detected_combos:
                rejected_count += 1
                continue
            candidate = detected_combos[0].strip()
            resolved = False
            for delimiter_char in COMBO_DELIMITER_PRIORITY:
                if delimiter_char in candidate:
                    delimiter_parts = candidate.split(delimiter_char, 1)
                    if len(delimiter_parts) == 2:
                        username_field = delimiter_parts[0].strip()
                        password_field = delimiter_parts[1].strip()
                        resolved = True
                        break
            if not resolved:
                rejected_count += 1
                continue

        if len(username_field) < MIN_FIELD_LENGTH or len(password_field) < MIN_FIELD_LENGTH:
            rejected_count += 1
            continue

        username_lower = username_field.lower()
        if username_lower == 'unknown' or not RX_LOGIN_VALIDATOR.fullmatch(username_field):
            rejected_count += 1
            continue

        tld_collision = (
            '.' in username_lower
            and '@' not in username_lower
            and any(username_lower.endswith(f'.{tld}') for tld in BLOCKED_TLD_SET)
        )
        path_collision = (
            '@' not in username_lower
            and any(marker in username_lower for marker in INVALID_PATH_MARKERS)
        )
        if any(marker in username_lower for marker in INVALID_USERNAME_MARKERS) or tld_collision or path_collision:
            rejected_count += 1
            continue

        sanitised_username = username_field.replace(' ', '')
        if len(sanitised_username) < MIN_FIELD_LENGTH:
            rejected_count += 1
            continue

        extracted_combos.append(f"{sanitised_username}:{password_field}")

    return extracted_combos, rejected_count


async def pipeline_combo_extraction(source_lines: List[str]) -> Tuple[List[str], int]:
    line_count = len(source_lines)
    active_batch_size = COMBO_LARGE_BATCH_SIZE if line_count > COMBO_BATCH_THRESHOLD else COMBO_SMALL_BATCH_SIZE
    aggregated_results: List[str] = []
    active_loop = asyncio.get_running_loop()

    for cursor in range(0, line_count, active_batch_size):
        current_batch = source_lines[cursor: cursor + active_batch_size]
        batch_combos, _ = await active_loop.run_in_executor(THREAD_POOL, scan_combo_batch, current_batch)
        aggregated_results.extend(batch_combos)
        await release_event_loop(cursor)

    unique_combos, duplicate_count = await deduplicate_and_order(aggregated_results)
    return unique_combos, duplicate_count


async def register(application: FastAPI) -> None:
    @application.get("/cmb")
    async def combo_search_endpoint(site: str):
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
                {"site": site, "combos": []},
                request_start, 0, 0,
            )

        unique_combos, duplicates_removed = await pipeline_combo_extraction(raw_lines)

        return forge_api_response(
            {"site": site, "combos": unique_combos},
            request_start,
            len(unique_combos),
            duplicates_removed,
        )