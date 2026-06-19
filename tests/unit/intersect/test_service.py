from __future__ import annotations

import pytest

from live_stream_analysis.intersect.data_models import IntersectConfig
from live_stream_analysis.intersect.service import LiveStreamAnalysisCapability, _event_key_for_name


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


def test_capability_declares_expected_event_names():
    assert sorted(LiveStreamAnalysisCapability.intersect_sdk_events) == ["histogram_updated", "run_completed"]


def test_event_key_for_name_maps_histogram_event():
    assert _event_key_for_name(_config(), "histogram.updated") == "histogram_updated"


def test_event_key_for_name_maps_run_complete_event():
    assert _event_key_for_name(_config(), "run.completed") == "run_completed"


def test_event_key_for_name_rejects_unknown_event():
    with pytest.raises(ValueError, match="Unsupported INTERSECT event name"):
        _event_key_for_name(_config(), "other.event")