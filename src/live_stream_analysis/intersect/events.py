from __future__ import annotations

import re
from typing import Any

from .data_models import HistogramEventPayload, RunCompleteEventPayload, RunMetadata


def build_histogram_payload(q_values: list[float], intensities: list[float], errors: list[float]) -> dict[str, Any]:
    return HistogramEventPayload(q=q_values, intensity=intensities, error=errors).model_dump(exclude_none=True)


def build_run_complete_payload(metadata: RunMetadata) -> dict[str, Any]:
    return RunCompleteEventPayload(**metadata.model_dump()).model_dump(exclude_none=True)


def infer_run_metadata(args) -> RunMetadata:
    source = args.adara_file
    if source is None and args.nexus_file:
        source = args.nexus_file[0]
    run_number = _extract_run_number(source) if source is not None else None
    ipts = _extract_ipts(source) if source is not None else None
    return RunMetadata(instrument="nomad", ipts=ipts, run_number=run_number)


def _extract_run_number(source: str | None) -> int | None:
    if not source:
        return None
    match = re.search(r"run[-_](\d+)|NOM[_-](\d+)", source)
    if match is None:
        return None
    for group in match.groups():
        if group is not None:
            return int(group)
    return None


def _extract_ipts(source: str | None) -> int | None:
    if not source:
        return None
    match = re.search(r"IPTS[-_](\d+)", source, re.IGNORECASE)
    if match is None:
        return None
    return int(match.group(1))