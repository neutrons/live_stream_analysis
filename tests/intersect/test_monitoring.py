from __future__ import annotations

from argparse import Namespace

from live_stream_analysis.intersect.data_models import RunMetadata
from live_stream_analysis.intersect.events import (
    build_histogram_payload,
    build_run_complete_payload,
    infer_run_metadata,
)


def test_build_histogram_payload_returns_typed_schema_shape():
    payload = build_histogram_payload([0.1, 0.2], [10.0, 20.0], [1.0, 2.0])

    assert payload == {
        "q": [0.1, 0.2],
        "intensity": [10.0, 20.0],
        "error": [1.0, 2.0],
    }


def test_build_run_complete_payload_omits_none_fields():
    payload = build_run_complete_payload(RunMetadata(instrument="nomad", ipts=None, run_number=243451))

    assert payload == {"instrument": "nomad", "run_number": 243451}


def test_infer_run_metadata_extracts_ipts_and_run_number_from_adara_path():
    args = Namespace(
        adara_file="/SNS/NOM/IPTS-12345/shared/run-243451/sample.adara",
        nexus_file=None,
    )

    metadata = infer_run_metadata(args)

    assert metadata.instrument == "nomad"
    assert metadata.ipts == 12345
    assert metadata.run_number == 243451


def test_infer_run_metadata_uses_first_nexus_file_when_adara_missing():
    args = Namespace(
        adara_file=None,
        nexus_file=["/tmp/NOM_243708.nxs.h5", "/tmp/NOM_243709.nxs.h5"],
    )

    metadata = infer_run_metadata(args)

    assert metadata.run_number == 243708