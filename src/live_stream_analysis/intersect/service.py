import csv
import io
import threading
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol

from intersect_sdk import (
    IntersectBaseCapabilityImplementation,
    IntersectEventDefinition,
    IntersectService,
    intersect_message,
    intersect_status,
)

from .config import build_service_config
from .data_models import (
    CsvTextRequest,
    HistogramEventPayload,
    IntersectConfig,
    RunCompleteEventPayload,
    ServiceStatusPayload,
    StartAdaraFileReadRequest,
    StartAdaraFileReadResponse,
    UpdateResponse,
)


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
    pixel_q_conversion: Any | None = None
    background_values: list[float] | None = None
    background_errors: list[float] | None = None
    normalization_values: list[float] | None = None
    normalization_errors: list[float] | None = None
    correction_bins: int | None = None
    adara_file_read_released: bool = True
    adara_file_read_event: threading.Event = field(default_factory=threading.Event)

    def __post_init__(self) -> None:
        if self.adara_file_read_released:
            self.adara_file_read_event.set()
        else:
            self.adara_file_read_event.clear()

    def configure_adara_file_read_gate(self, released: bool) -> None:
        self.adara_file_read_released = released
        if released:
            self.adara_file_read_event.set()
        else:
            self.adara_file_read_event.clear()

    def wait_for_adara_file_read_release(self, timeout: float | None = None) -> bool:
        return self.adara_file_read_event.wait(timeout=timeout)

    def release_adara_file_read(self) -> None:
        self.configure_adara_file_read_gate(True)

    def validate_correction_length(self, values: list[float], kind: str) -> None:
        if self.correction_bins is None:
            self.correction_bins = len(values)
            return
        if len(values) != self.correction_bins:
            raise ValueError(
                f"{kind} correction CSV has {len(values)} bins but expected {self.correction_bins}"
            )


def _load_correction_from_csv_text(csv_text: str) -> tuple[list[float], list[float]]:
    reader = csv.DictReader(io.StringIO(csv_text))
    required_columns = {"Q value", "I(Q)", "Error I(Q)"}
    if reader.fieldnames is None or not required_columns.issubset(set(reader.fieldnames)):
        raise ValueError("Correction CSV must include columns: 'Q value', 'I(Q)', 'Error I(Q)'")

    values: list[float] = []
    errors: list[float] = []
    for row in reader:
        values.append(float(row["I(Q)"]))
        errors.append(float(row["Error I(Q)"]))
    return values, errors


def _load_pixel_q_conversion_from_csv_text(csv_text: str) -> Any:
    reader = csv.DictReader(io.StringIO(csv_text))
    required_columns = {"pixel id", "TOF-to-Q matrix element"}
    if reader.fieldnames is None or not required_columns.issubset(set(reader.fieldnames)):
        raise ValueError("Pixel geometry CSV must include columns: 'pixel id' and 'TOF-to-Q matrix element'")

    rows = list(reader)
    if not rows:
        raise ValueError("No detector rows found in pixel geometry CSV")

    max_pixel_id = max(int(row["pixel id"]) for row in rows)
    q_matrix_constants = [0.0] * (max_pixel_id + 1)
    difc_values = [0.0] * (max_pixel_id + 1)
    difa_values = [0.0] * (max_pixel_id + 1)
    tzero_values = [0.0] * (max_pixel_id + 1)
    use_values = [1] * (max_pixel_id + 1)

    for row in rows:
        pixel_id = int(row["pixel id"])
        q_matrix_constants[pixel_id] = float(row["TOF-to-Q matrix element"])
        difc_values[pixel_id] = float(row.get("difc", 0.0) or 0.0)
        difa_values[pixel_id] = float(row.get("difa", 0.0) or 0.0)
        tzero_values[pixel_id] = float(row.get("tzero", 0.0) or 0.0)
        use_values[pixel_id] = int(float(row.get("use", 1) or 1))

    from ..analyzer.histogram import PixelQConversion

    return PixelQConversion(
        q_matrix_constants=q_matrix_constants,
        difc=difc_values,
        difa=difa_values,
        tzero=tzero_values,
        use=use_values,
    )


class LiveStreamAnalysisCapability(IntersectBaseCapabilityImplementation):
    intersect_sdk_capability_name = "nomadanalysis"
    intersect_sdk_events: ClassVar[dict[str, IntersectEventDefinition]] = {
        "histogram_updated": IntersectEventDefinition(event_type=HistogramEventPayload),
        "run_completed": IntersectEventDefinition(event_type=RunCompleteEventPayload),
    }

    def __init__(self, runtime_state: HistogramRuntimeState | None = None):
        super().__init__()
        self.runtime_state = runtime_state or HistogramRuntimeState()

    @intersect_status()
    def status(self) -> ServiceStatusPayload:
        return ServiceStatusPayload(status="up")

    @intersect_message()
    def set_background(self, payload: CsvTextRequest) -> UpdateResponse:
        values, errors = _load_correction_from_csv_text(payload.csv_text)
        self.runtime_state.validate_correction_length(values, "Background")
        self.runtime_state.background_values = values
        self.runtime_state.background_errors = errors
        return UpdateResponse(status="updated", kind="background")

    @intersect_message()
    def set_normalization(self, payload: CsvTextRequest) -> UpdateResponse:
        values, errors = _load_correction_from_csv_text(payload.csv_text)
        self.runtime_state.validate_correction_length(values, "Normalization")
        self.runtime_state.normalization_values = values
        self.runtime_state.normalization_errors = errors
        return UpdateResponse(status="updated", kind="normalization")

    @intersect_message()
    def set_pixel_geometry_conversion(self, payload: CsvTextRequest) -> UpdateResponse:
        self.runtime_state.pixel_q_conversion = _load_pixel_q_conversion_from_csv_text(payload.csv_text)
        return UpdateResponse(status="updated", kind="pixel_geometry_conversion")

    @intersect_message()
    def start_adara_file_read(self, payload: StartAdaraFileReadRequest) -> StartAdaraFileReadResponse:
        self.runtime_state.configure_adara_file_read_gate(payload.release)
        return StartAdaraFileReadResponse(
            status="updated",
            kind="adara_file_read",
            released=self.runtime_state.adara_file_read_released,
        )


class IntersectEventPublisher:
    def __init__(self, config: IntersectConfig, runtime_state: HistogramRuntimeState | None = None):
        self._config = config
        self._capability = LiveStreamAnalysisCapability(runtime_state=runtime_state)
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
    return IntersectEventPublisher(config, runtime_state=runtime_state)


def _event_key_for_name(config: IntersectConfig, event_name: str) -> str:
    if event_name == config.histogram_event_name:
        return "histogram_updated"
    if event_name == config.run_complete_event_name:
        return "run_completed"
    raise ValueError(f"Unsupported INTERSECT event name: {event_name}")