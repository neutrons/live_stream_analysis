from pydantic import BaseModel, ConfigDict, Field


class HistogramEventPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q: list[float]
    intensity: list[float]
    error: list[float]


class RunCompleteEventPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument: str
    ipts: int | None = None
    run_number: int | None = None


class RunMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument: str = "nomad"
    ipts: int | None = None
    run_number: int | None = None


class ServiceStatusPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str


class IntersectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_name: str = Field(default="live-stream-analysis")
    publish_interval_seconds: int = Field(default=5, ge=1)
    histogram_event_name: str
    run_complete_event_name: str
    hierarchy: dict[str, str] = Field(default_factory=dict)
    broker: dict[str, str | int | bool] = Field(default_factory=dict)
    data_store: dict[str, str | int | bool] = Field(default_factory=dict)


class CsvTextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    csv_text: str = Field(min_length=1)


class StartAdaraFileReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    release: bool = True


class UpdateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    kind: str


class StartAdaraFileReadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    kind: str
    released: bool


CsvTextRequest.model_rebuild()
StartAdaraFileReadRequest.model_rebuild()
StartAdaraFileReadResponse.model_rebuild()
UpdateResponse.model_rebuild()
ServiceStatusPayload.model_rebuild()
