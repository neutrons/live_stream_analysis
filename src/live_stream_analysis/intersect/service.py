from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

from intersect_sdk import IntersectBaseCapabilityImplementation, IntersectEventDefinition, IntersectService, intersect_status

from .config import build_service_config
from .data_models import HistogramEventPayload, IntersectConfig, RunCompleteEventPayload, ServiceStatusPayload


class EventPublisher(Protocol):
    def publish_event(self, event_name: str, payload: dict[str, Any]) -> None: ...

    def close(self) -> None: ...


class NullEventPublisher:
    def publish_event(self, event_name: str, payload: dict[str, Any]) -> None:
        _ = (event_name, payload)

    def close(self) -> None:
        return None


@dataclass(slots=True)
class HistogramRuntimeState:
    pixel_q_conversion: Any
    background_values: list[float] | None = None
    background_errors: list[float] | None = None
    normalization_values: list[float] | None = None
    normalization_errors: list[float] | None = None


class LiveStreamAnalysisCapability(IntersectBaseCapabilityImplementation):
    intersect_sdk_capability_name = "nomadanalysis"
    intersect_sdk_events: ClassVar[dict[str, IntersectEventDefinition]] = {
        "histogram_updated": IntersectEventDefinition(event_type=HistogramEventPayload),
        "run_completed": IntersectEventDefinition(event_type=RunCompleteEventPayload),
    }

    @intersect_status()
    def status(self) -> ServiceStatusPayload:
        return ServiceStatusPayload(status="up")


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


def create_event_publisher(
    config: IntersectConfig,
    runtime_state: HistogramRuntimeState | None = None,
) -> EventPublisher:
    _ = runtime_state
    return IntersectEventPublisher(config)


def _event_key_for_name(config: IntersectConfig, event_name: str) -> str:
    if event_name == config.histogram_event_name:
        return "histogram_updated"
    if event_name == config.run_complete_event_name:
        return "run_completed"
    raise ValueError(f"Unsupported INTERSECT event name: {event_name}")