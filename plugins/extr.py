import asyncio
import re
import time
from typing import Dict, List, Tuple

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

CREDENTIAL_REGEX_MAP: Dict[str, str] = {
    "mailpass": r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})([:|])([^\s]+)',
    "userpass": r'([a-zA-Z0-9_-]{4,})([:|])([^\s]+)',
    "num_pass":  r'((?:\+?)\d[\d\s\-\(\)]*?\d)([:|])([^\s]+)',
}

STRUCTURAL_REGEX_MAP: Dict[str, re.Pattern] = {
    "domain": re.compile(
        r'\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}\b'
    ),
    "url": re.compile(
        r'https?://(?:[-\w.])+(?:[:\d]+)?(?:/(?:[\w/_.])*(?:\?(?:[\w&=%.]*)?)?(?:#(?:[\w.])*)?)?'
    ),
}

COMPILED_CREDENTIAL_MAP: Dict[str, re.Pattern] = {
    key: re.compile(pattern) for key, pattern in CREDENTIAL_REGEX_MAP.items()
}

ACCEPTED_FORMAT_KEYS: List[str] = list(CREDENTIAL_REGEX_MAP.keys()) + list(STRUCTURAL_REGEX_MAP.keys())

EXTRACTION_BATCH_THRESHOLD: int = 10000
EXTRACTION_LARGE_BATCH_SIZE: int = 2500
EXTRACTION_SMALL_BATCH_SIZE: int = 1000
RX_PHONE_NORMALISER = re.compile(r'[\s\-\(\)]')
MIN_CREDENTIAL_LENGTH: int = 3


def scan_credential_batch(raw_batch: List[str], format_key: str) -> Tuple[List[str], int]:
    active_pattern = COMPILED_CREDENTIAL_MAP[format_key]
    extracted_credentials: List[str] = []
    match_tally: int = 0
    fingerprint_registry: set = set()

    for raw_line in raw_batch:
        stripped = raw_line.strip()
        if not stripped or is_record_blacklisted(stripped):
            continue

        pattern_matches = active_pattern.findall(stripped)
        if not pattern_matches:
            continue

        match_tally += len(pattern_matches)
        identifier_val, separator_char, password_val = pattern_matches[-1]

        if len(identifier_val) < MIN_CREDENTIAL_LENGTH or len(password_val) < MIN_CREDENTIAL_LENGTH:
            continue

        dedup_key = identifier_val
        if format_key == "num_pass":
            dedup_key = RX_PHONE_NORMALISER.sub('', identifier_val)

        normalised_fingerprint = dedup_key.lower()
        if normalised_fingerprint not in fingerprint_registry:
            fingerprint_registry.add(normalised_fingerprint)
            extracted_credentials.append(f"{identifier_val}{separator_char}{password_val}")

    return extracted_credentials, match_tally


def scan_structural_batch(raw_batch: List[str], format_key: str) -> List[str]:
    active_pattern = STRUCTURAL_REGEX_MAP[format_key]
    extracted_tokens: List[str] = []

    for raw_line in raw_batch:
        stripped = raw_line.strip()
        if not stripped or is_record_blacklisted(stripped):
            continue

        pattern_matches = active_pattern.findall(stripped)
        for matched_token in pattern_matches:
            if isinstance(matched_token, tuple):
                extracted_tokens.append(''.join(matched_token))
            else:
                extracted_tokens.append(str(matched_token))

    return extracted_tokens


async def pipeline_extraction(source_lines: List[str], format_key: str) -> Tuple[List[str], int]:
    line_count = len(source_lines)
    active_batch_size = EXTRACTION_LARGE_BATCH_SIZE if line_count > EXTRACTION_BATCH_THRESHOLD else EXTRACTION_SMALL_BATCH_SIZE
    aggregated_results: List[str] = []
    active_loop = asyncio.get_running_loop()

    if format_key in COMPILED_CREDENTIAL_MAP:
        for cursor in range(0, line_count, active_batch_size):
            current_batch = source_lines[cursor: cursor + active_batch_size]
            batch_credentials, _ = await active_loop.run_in_executor(
                THREAD_POOL, scan_credential_batch, current_batch, format_key
            )
            aggregated_results.extend(batch_credentials)
            await release_event_loop(cursor)
    else:
        for cursor in range(0, line_count, active_batch_size):
            current_batch = source_lines[cursor: cursor + active_batch_size]
            batch_tokens = await active_loop.run_in_executor(
                THREAD_POOL, scan_structural_batch, current_batch, format_key
            )
            aggregated_results.extend(batch_tokens)
            await release_event_loop(cursor)

    unique_results, duplicate_count = await deduplicate_and_order(aggregated_results)
    return unique_results, duplicate_count


async def register(application: FastAPI) -> None:
    @application.get("/extr")
    async def extraction_search_endpoint(site: str, format: str):
        if not site.strip():
            raise HTTPException(status_code=400, detail="Parameter 'site' must not be empty")
        if format not in ACCEPTED_FORMAT_KEYS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported format. Accepted values: {', '.join(ACCEPTED_FORMAT_KEYS)}",
            )

        request_start = time.perf_counter()
        datastore_paths = collect_datastore_paths(__file__)

        rg_arguments = ["rg", "-i", "--no-heading", "--no-line-number", "--no-filename"]
        if format in CREDENTIAL_REGEX_MAP:
            rg_arguments += ["-e", CREDENTIAL_REGEX_MAP[format]]
        else:
            rg_arguments += ["--fixed-strings", site]
        rg_arguments += datastore_paths

        exit_code, stdout_data, stderr_data = await invoke_search_engine(rg_arguments)
        if exit_code not in (0, 1):
            raise HTTPException(status_code=500, detail=f"Search engine error: {stderr_data.strip()}")

        raw_lines = tokenize_output_lines(stdout_data)

        if not raw_lines:
            return forge_api_response(
                {"site": site, "format": format, "matches": []},
                request_start, 0, 0,
            )

        unique_matches, duplicates_removed = await pipeline_extraction(raw_lines, format)

        return forge_api_response(
            {"site": site, "format": format, "matches": unique_matches},
            request_start,
            len(unique_matches),
            duplicates_removed,
        )