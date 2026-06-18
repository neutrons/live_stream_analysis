from __future__ import annotations

from typing import Any, ClassVar, Protocol

from intersect_sdk import IntersectBaseCapabilityImplementation, IntersectEventDefinition, IntersectService, intersect_status

from .config import build_service_config
from .data_models import IntersectConfig


class EventPublisher(Protocol):
    def publish_event(self, event_name: str, payload: dict[str, Any]) -> None: ...

    def close(self) -> None: ...


class NullEventPublisher:
    def publish_event(self, event_name: str, payload: dict[str, Any]) -> None:
        _ = (event_name, payload)

    def close(self) -> None:
        return None


class LiveStreamAnalysisCapability(IntersectBaseCapabilityImplementation):
    intersect_sdk_capability_name = "nomadanalysis"
    intersect_sdk_events: ClassVar[dict[str, IntersectEventDefinition]] = {
        "histogram_updated": IntersectEventDefinition(event_type=dict[str, Any]),
        "run_completed": IntersectEventDefinition(event_type=dict[str, Any]),
    }

    @intersect_status()
    def status(self) -> dict[str, str]:
        return {"status": "up"}


class IntersectEventPublisher:
    def __init__(self, config: IntersectConfig):
        self._config = config
        self._capability = LiveStreamAnalysisCapability()
        self._service = IntersectService([self._capability], build_service_config(config))
        self._service.startup()

    def publish_event(self, event_name: str, payload: dict[str, Any]) -> None:
        self._capability.intersect_sdk_emit_event(_event_key_for_name(self._config, event_name), payload)

    def close(self) -> None:
        self._service.shutdown(reason="live-stream-analysis shutdown")


def create_event_publisher(config: IntersectConfig) -> EventPublisher:
    return IntersectEventPublisher(config)


def _event_key_for_name(config: IntersectConfig, event_name: str) -> str:
    if event_name == config.histogram_event_name:
        return "histogram_updated"
    if event_name == config.run_complete_event_name:
        return "run_completed"
    raise ValueError(f"Unsupported INTERSECT event name: {event_name}")