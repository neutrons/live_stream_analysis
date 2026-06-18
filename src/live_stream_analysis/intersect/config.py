from __future__ import annotations

from pathlib import Path
import re

from intersect_sdk import HierarchyConfig, IntersectClientCallback, IntersectClientConfig, IntersectEventMessageParams, IntersectServiceConfig
from intersect_sdk_common import ControlPlaneConfig, DataStoreConfig, DataStoreConfigMap
import yaml

from .data_models import IntersectConfig


def load_intersect_config(path: str | Path) -> IntersectConfig:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    service = raw.get("service") or {}
    events = raw.get("events")
    if not isinstance(events, dict):
        raise ValueError("INTERSECT config must define events.histogram and events.run_complete")

    histogram_event_name = events.get("histogram")
    run_complete_event_name = events.get("run_complete")
    if not histogram_event_name or not run_complete_event_name:
        raise ValueError("INTERSECT config must define events.histogram and events.run_complete")

    return IntersectConfig(
        service_name=str(service.get("name", "live-stream-analysis")),
        publish_interval_seconds=int(service.get("publish_interval_seconds", 5)),
        histogram_event_name=str(histogram_event_name),
        run_complete_event_name=str(run_complete_event_name),
        hierarchy=dict(raw.get("hierarchy") or {}),
        broker=dict(raw.get("broker") or {}),
        data_store=dict(raw.get("data_store") or {}),
    )


def build_service_config(config: IntersectConfig) -> IntersectServiceConfig:
    hierarchy = config.hierarchy or {}
    return IntersectServiceConfig(
        hierarchy=HierarchyConfig(
            organization=hierarchy.get("organization", "ornl"),
            facility=hierarchy.get("facility", "neutrons"),
            system=hierarchy.get("system", "nomad"),
            subsystem=hierarchy.get("subsystem"),
            service=hierarchy.get("service", _normalize_hierarchy_name(config.service_name)),
        ),
        brokers=[
            ControlPlaneConfig(
                protocol=str(config.broker.get("protocol", "amqp0.9.1")),
                username=str(config.broker["username"]),
                password=str(config.broker["password"]),
                host=str(config.broker.get("host", "127.0.0.1")),
                port=int(config.broker.get("port", 5672)),
                is_root=bool(config.broker.get("is_root", True)),
            )
        ],
        data_stores=DataStoreConfigMap(
            minio=[
                DataStoreConfig(
                    username=str(config.data_store["username"]),
                    password=str(config.data_store["password"]),
                    host=str(config.data_store.get("host", "127.0.0.1")),
                    port=int(config.data_store.get("port", 9000)),
                )
            ]
            if config.data_store
            else []
        ),
    )


def build_client_config(config: IntersectConfig, capability_name: str = "nomadanalysis") -> IntersectClientConfig:
    service_config = build_service_config(config)
    hierarchy = service_config.hierarchy.hierarchy_string(".")
    return IntersectClientConfig(
        brokers=service_config.brokers,
        data_stores=service_config.data_stores,
        organization=service_config.hierarchy.organization,
        facility=service_config.hierarchy.facility,
        system=service_config.hierarchy.system,
        initial_message_event_config=IntersectClientCallback(
            services_to_start_listening_for_events=[
                IntersectEventMessageParams(
                    hierarchy=hierarchy,
                    capability_name=capability_name,
                    event_name="histogram_updated",
                ),
                IntersectEventMessageParams(
                    hierarchy=hierarchy,
                    capability_name=capability_name,
                    event_name="run_completed",
                ),
            ]
        ),
    )


def _normalize_hierarchy_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if len(normalized) < 3:
        return "lsa"
    return normalized[:63]