from __future__ import annotations

import json

from live_stream_analysis.intersect.config import build_service_config
from live_stream_analysis.intersect.data_models import IntersectConfig, UpdateResponse
from live_stream_analysis.intersect.service import HistogramRuntimeState, LiveStreamAnalysisCapability
from intersect_sdk import IntersectService


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


def test_set_background_dispatches_through_intersect_service_operation_map():
    runtime_state = HistogramRuntimeState()
    capability = LiveStreamAnalysisCapability(runtime_state=runtime_state)
    service = IntersectService([capability], build_service_config(_config()))

    operation = "nomadanalysis.set_background"
    operation_meta = service._function_map[operation]  # noqa: SLF001
    response_bytes = service._call_user_function(  # noqa: SLF001
        capability,
        "set_background",
        operation_meta,
        json.dumps(
            {
                "csv_text": "Q value,I(Q),Error I(Q)\n0.1,10.0,1.0\n0.2,20.0,2.0\n"
            }
        ).encode("utf-8"),
    )

    response = UpdateResponse.model_validate_json(response_bytes)

    assert response == UpdateResponse(status="updated", kind="background")
    assert runtime_state.background_values == [10.0, 20.0]
    assert runtime_state.background_errors == [1.0, 2.0]