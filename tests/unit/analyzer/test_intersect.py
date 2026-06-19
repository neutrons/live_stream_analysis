from __future__ import annotations

from pathlib import Path

import pytest
from intersect_sdk import get_schema_from_capability_implementations
from intersect_sdk_common import HierarchyConfig

from live_stream_analysis.intersect import (
    IntersectConfig,
    RunMetadata,
    build_client_config,
    build_run_complete_payload,
    build_service_config,
    load_intersect_config,
)
from live_stream_analysis.intersect.service import LiveStreamAnalysisCapability


def test_load_intersect_config_reads_publish_interval_and_topics(tmp_path: Path):
    config_path = tmp_path / "intersect.yaml"
    config_path.write_text(
        "\n".join(
            [
                "service:",
                "  name: live-stream-analysis",
                "  publish_interval_seconds: 5",
                "hierarchy:",
                "  organization: ornl",
                "  facility: neutrons",
                "  system: nomad",
                "  subsystem: analysis",
                "  service: livestreamanalysis",
                "events:",
                "  histogram: histogram.updated",
                "  run_complete: run.completed",
                "broker:",
                "  protocol: amqp0.9.1",
                "  host: localhost",
                "  port: 5672",
                "  username: guest",
                "  password: guest",
                "data_store:",
                "  host: minio",
                "  port: 9000",
                "  username: minioadmin",
                "  password: minioadmin",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    config = load_intersect_config(config_path)

    assert isinstance(config, IntersectConfig)
    assert config.service_name == "live-stream-analysis"
    assert config.publish_interval_seconds == 5
    assert config.histogram_event_name == "histogram.updated"
    assert config.run_complete_event_name == "run.completed"
    assert config.hierarchy["system"] == "nomad"
    assert config.broker["protocol"] == "amqp0.9.1"
    assert config.broker["host"] == "localhost"
    assert config.data_store["host"] == "minio"


def test_load_intersect_config_requires_event_names(tmp_path: Path):
    config_path = tmp_path / "intersect.yaml"
    config_path.write_text("service:\n  name: test\n", encoding="utf-8")

    with pytest.raises(ValueError, match="events"):
        load_intersect_config(config_path)


def test_build_run_complete_payload_omits_unknown_ipts():
    payload = build_run_complete_payload(
        RunMetadata(instrument="nomad", ipts=None, run_number=243451)
    )

    assert payload.model_dump(exclude_none=True) == {"instrument": "nomad", "run_number": 243451}


def test_build_run_complete_payload_includes_known_ipts():
    payload = build_run_complete_payload(
        RunMetadata(instrument="nomad", ipts=12345, run_number=243451)
    )

    assert payload.model_dump(exclude_none=True) == {"instrument": "nomad", "ipts": 12345, "run_number": 243451}


def test_build_service_config_uses_amqp_and_minio_defaults(tmp_path: Path):
    config_path = tmp_path / "intersect.yaml"
    config_path.write_text(
        "\n".join(
            [
                "service:",
                "  name: live-stream-analysis",
                "events:",
                "  histogram: histogram.updated",
                "  run_complete: run.completed",
                "broker:",
                "  protocol: amqp0.9.1",
                "  host: rabbitmq",
                "  port: 5672",
                "  username: guest",
                "  password: guest",
                "data_store:",
                "  host: minio",
                "  port: 9000",
                "  username: minioadmin",
                "  password: minioadmin",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    service_config = build_service_config(load_intersect_config(config_path))

    assert service_config.brokers[0].protocol == "amqp0.9.1"
    assert service_config.brokers[0].host == "rabbitmq"
    assert service_config.data_stores.minio[0].host == "minio"


def test_build_client_config_subscribes_to_both_events(tmp_path: Path):
    config_path = tmp_path / "intersect.yaml"
    config_path.write_text(
        "\n".join(
            [
                "service:",
                "  name: live-stream-analysis",
                "events:",
                "  histogram: histogram.updated",
                "  run_complete: run.completed",
                "broker:",
                "  protocol: amqp0.9.1",
                "  host: rabbitmq",
                "  port: 5672",
                "  username: guest",
                "  password: guest",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    client_config = build_client_config(load_intersect_config(config_path))

    event_names = [
        item.event_name for item in client_config.initial_message_event_config.services_to_start_listening_for_events
    ]
    assert event_names == ["histogram_updated", "run_completed"]


def test_generated_intersect_schema_includes_live_update_endpoints_and_models():
    schema = get_schema_from_capability_implementations(
        [LiveStreamAnalysisCapability],
        hierarchy=HierarchyConfig(
            organization="ornl",
            facility="neutrons",
            system="nomad",
            subsystem="analysis",
            service="livestreamanalysis",
        ),
    )

    capability_schema = schema["capabilities"]["nomadanalysis"]
    endpoints = capability_schema["endpoints"]

    for endpoint_name in ["set_background", "set_normalization", "set_pixel_geometry_conversion"]:
        assert endpoints[endpoint_name]["subscribe"]["message"]["payload"] == {
            "$ref": "#/components/schemas/CsvTextRequest"
        }
        assert endpoints[endpoint_name]["publish"]["message"]["payload"] == {
            "$ref": "#/components/schemas/UpdateResponse"
        }

    assert capability_schema["events"]["histogram_updated"]["payload"] == {
        "$ref": "#/components/schemas/HistogramEventPayload"
    }
    assert capability_schema["events"]["run_completed"]["payload"] == {
        "$ref": "#/components/schemas/RunCompleteEventPayload"
    }

    component_schemas = schema["components"]["schemas"]
    assert "CsvTextRequest" in component_schemas
    assert "UpdateResponse" in component_schemas