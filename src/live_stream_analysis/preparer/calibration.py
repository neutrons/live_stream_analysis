"""Helpers for reading Mantid Diffraction Calibration HDF5 files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import h5py


@dataclass(frozen=True, slots=True)
class DetectorCalibration:
    """Per-detector calibration parameters used in TOF -> d -> Q conversion."""

    difc: float
    difa: float
    tzero: float
    use: int


def load_diffraction_calibration(calibration_file: Path) -> dict[int, DetectorCalibration]:
    """Load detid->(difc,difa,tzero,use) from a Mantid diffcal HDF5 file."""
    path = calibration_file.resolve()
    if not path.exists():
        raise FileNotFoundError(f"Calibration file does not exist: {path}")

    with h5py.File(path, "r") as handle:
        if "calibration" not in handle:
            raise ValueError("Calibration file is missing '/calibration' group")

        group = handle["calibration"]
        if "detid" not in group or "difc" not in group:
            raise ValueError("Calibration file must include '/calibration/detid' and '/calibration/difc'")

        detid = group["detid"][:]
        difc = group["difc"][:]
        difa = group["difa"][:] if "difa" in group else None
        tzero = group["tzero"][:] if "tzero" in group else None
        use = group["use"][:] if "use" in group else None

    row_count = int(len(detid))
    if len(difc) != row_count:
        raise ValueError("Calibration file has mismatched detid/difc lengths")
    if difa is not None and len(difa) != row_count:
        raise ValueError("Calibration file has mismatched detid/difa lengths")
    if tzero is not None and len(tzero) != row_count:
        raise ValueError("Calibration file has mismatched detid/tzero lengths")
    if use is not None and len(use) != row_count:
        raise ValueError("Calibration file has mismatched detid/use lengths")

    by_detector: dict[int, DetectorCalibration] = {}
    for index in range(row_count):
        detector_id = int(detid[index])
        by_detector[detector_id] = DetectorCalibration(
            difc=float(difc[index]),
            difa=float(difa[index]) if difa is not None else 0.0,
            tzero=float(tzero[index]) if tzero is not None else 0.0,
            use=int(use[index]) if use is not None else 1,
        )

    return by_detector
