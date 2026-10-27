import io
import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol

from intersect_sdk import (
    IntersectBaseCapabilityImplementation,
    IntersectEventDefinition,
    IntersectService,
    intersect_message,
    intersect_status,
)

from ..correction_totals import CorrectionTotals
from ..statistics import RunStatistics
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

LOGGER = logging.getLogger(__name__)


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
    background_totals: CorrectionTotals | None = None
    normalization_values: list[float] | None = None
    normalization_errors: list[float] | None = None
    normalization_totals: CorrectionTotals | None = None
    adara_file_read_released: bool = True
    adara_file_read_event: threading.Event = field(default_factory=threading.Event)
    run_statistics: RunStatistics = field(default_factory=RunStatistics)
    # Corrections are replaced from the INTERSECT and dashboard threads while the analyzer
    # thread applies them, so values, errors and totals are swapped and read as one unit.
    correction_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    # Installed by the running analysis: takes the correction kind ("background" or
    # "normalization"), its CSV text and the name of where it came from, applies it the way a
    # dashboard upload is applied, and returns a summary.
    correction_uploader: Callable[[str, str, str], str] | None = field(default=None, repr=False)

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

    def set_background(self, values: list[float], errors: list[float], totals: CorrectionTotals | None) -> None:
        with self.correction_lock:
            self.background_values = values
            self.background_errors = errors
            self.background_totals = totals

    def background(self) -> tuple[list[float], list[float], CorrectionTotals | None] | None:
        with self.correction_lock:
            if self.background_values is None:
                return None
            errors = self.background_errors or [0.0] * len(self.background_values)
            return self.background_values, errors, self.background_totals

    def set_normalization(self, values: list[float], errors: list[float], totals: CorrectionTotals | None) -> None:
        with self.correction_lock:
            self.normalization_values = values
            self.normalization_errors = errors
            self.normalization_totals = totals

    def normalization(self) -> tuple[list[float], list[float], CorrectionTotals | None] | None:
        with self.correction_lock:
            if self.normalization_values is None:
                return None
            errors = self.normalization_errors or [0.0] * len(self.normalization_values)
            return self.normalization_values, errors, self.normalization_totals


def _load_pixel_q_conversion_from_csv_text(csv_text: str) -> Any:
    from ..analyzer.histogram import parse_pixel_q_conversion

    return parse_pixel_q_conversion(io.StringIO(csv_text))


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
        """Replace the background mid-run, exactly as a CSV uploaded to the dashboard does."""
        return self._upload_correction("background", payload)

    @intersect_message()
    def set_normalization(self, payload: CsvTextRequest) -> UpdateResponse:
        """Replace the normalization mid-run, exactly as a CSV uploaded to the dashboard does."""
        return self._upload_correction("normalization", payload)

    def _upload_correction(self, kind: str, payload: CsvTextRequest) -> UpdateResponse:
        uploader = self.runtime_state.correction_uploader
        if uploader is None:
            raise ValueError(f"set_{kind} has no running analysis to bin the {kind} onto")
        message = uploader(kind, payload.csv_text, f"INTERSECT set_{kind}")
        return UpdateResponse(status="updated", kind=kind, message=message)

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
        self._queue: queue.Queue[tuple[str, dict[str, Any]] | None] = queue.Queue(maxsize=8)
        self._worker = threading.Thread(target=self._publish_worker, name="intersect-event-publisher", daemon=True)
        self._service.startup()
        self._worker.start()

    def publish_event(self, event_name: str, payload: dict[str, Any]) -> None:
        try:
            self._queue.put_nowait((event_name, payload))
        except queue.Full:
            try:
                dropped = self._queue.get_nowait()
            except queue.Empty:
                dropped = None
            if dropped is not None:
                LOGGER.warning(
                    "Dropping stale INTERSECT event '%s' due to publisher backpressure",
                    dropped[0],
                )
            self._queue.put_nowait((event_name, payload))

    def close(self) -> None:
        self._queue.put(None)
        self._worker.join(timeout=2.0)
        self._service.shutdown(reason="live-stream-analysis shutdown")

    def _publish_worker(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            event_name, payload = item
            try:
                self._capability.intersect_sdk_emit_event(_event_key_for_name(self._config, event_name), payload)
            except Exception:
                LOGGER.exception("Failed to publish INTERSECT event '%s'", event_name)


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
