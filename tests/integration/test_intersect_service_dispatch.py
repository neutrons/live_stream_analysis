from __future__ import annotations

import json

import pytest
from intersect_sdk import IntersectService

from live_stream_analysis.intersect.config import build_service_config
from live_stream_analysis.intersect.data_models import IntersectConfig, UpdateResponse
from live_stream_analysis.intersect.service import HistogramRuntimeState, LiveStreamAnalysisCapability


def _config() -> IntersectConfig:
    return IntersectConfig(
        service_name="live-stream-analysis",
        publish_interval_seconds=5,
        histogram_event_name="histogram.updated",
        run_complete_event_name="run.completed",
        hierarchy={},
        broker={"username": "guest", "password": "guest"},
        data_store={},
    )


@pytest.mark.parametrize("kind", ["background", "normalization"])
def test_set_correction_dispatches_through_intersect_service_operation_map(kind):
    runtime_state = HistogramRuntimeState()
    received: list[tuple[str, str]] = []

    def uploader(upload_kind: str, csv_text: str, _source: str) -> str:
        received.append((upload_kind, csv_text))
        return "covers 2 of 2 bins"

    runtime_state.correction_uploader = uploader
    capability = LiveStreamAnalysisCapability(runtime_state=runtime_state)
    service = IntersectService([capability], build_service_config(_config()))

    operation = f"nomadanalysis.set_{kind}"
    operation_meta = service._function_map[operation]  # noqa: SLF001
    response_bytes = service._call_user_function(  # noqa: SLF001
        capability,
        f"set_{kind}",
        operation_meta,
        json.dumps({"csv_text": "Q value,I(Q),Error I(Q)\n0.1,10.0,1.0\n0.2,20.0,2.0\n"}).encode("utf-8"),
    )

    response = UpdateResponse.model_validate_json(response_bytes)

    assert response == UpdateResponse(status="updated", kind=kind, message="covers 2 of 2 bins")
    assert received == [(kind, "Q value,I(Q),Error I(Q)\n0.1,10.0,1.0\n0.2,20.0,2.0\n")]
