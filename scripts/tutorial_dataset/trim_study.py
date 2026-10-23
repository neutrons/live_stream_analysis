#!/usr/bin/env python3
"""How far can each tutorial file be trimmed and still reproduce the full-data result?

Each input is trimmed to the first N pulses of its run (see trim.py), for a log-spaced range of
N, one file at a time with the other two at full size. Each trimmed set is reduced to a
corrected I(Q), using the package's own corrections, and compared with the full-data result:

- **R**, the relative RMS difference of the corrected I(Q) over Q 1-20 1/A, with
  similarity = 1 - R;
- the largest shift among the strong diamond peaks, from their full-data positions;
- the mean sigma/I(Q) over Q 2-18 1/A, which should scale as 1/sqrt(events).

Counting noise makes R^2 fall as 1/N, so each file is fit with R^2 = k^2/N + c^2, where c is a
floor that does not shrink with N. The sample dominates the combined R, so the tutorial trims give
the sample as many pulses as the ADARA file holds under the repository's 8 MB file limit, and each
correction the smallest trim that adds at most 5 % to the sample's R. Those picks are then checked
by combining the actual trimmed files.

Trims and reductions already in the work directory are reused, so re-running is quick.

Samples are histogrammed once, without corrections, and the corrections are applied offline, so
any combination of sample, background and normalization costs a fraction of a second.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from trim import trim_adara, trim_nexus  # noqa: E402

from live_stream_analysis.analyzer.histogram import apply_corrections  # noqa: E402
from live_stream_analysis.intersect.service import HistogramRuntimeState  # noqa: E402
from live_stream_analysis.notebook import read_csv  # noqa: E402
from live_stream_analysis.preparer import reduce_nexus_files, write_reduction_csv  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
RUN_DIR = REPO.parent / "nomad-adara-carbon-share" / "20260619-140639.403016382-run-244114"
INPUTS = {
    "nexus_diamond": REPO / "nexus_files/diamond/NOM_244114.nxs.h5",
    "adara_diamond": (
        RUN_DIR / "m00000001-f00000001-run-244114.adara",
        RUN_DIR / "m00000001-f00000016-run-244114.adara",
    ),
    "nexus_background": REPO / "nexus_files/mt_pac_can/NOM_243710.nxs.h5",
    "nexus_normalization": REPO / "nexus_files/vanadium/NOM_243712.nxs.h5",
}
FULL_BACKGROUND = [REPO / "nexus_files/mt_pac_can/NOM_243710.nxs.h5", REPO / "nexus_files/mt_pac_can/NOM_243711.nxs.h5"]
GEOMETRY = REPO / "notebooks/output/pixel_geometry_calibrated.csv"
REFLECTIONS = REPO / "notebooks/output/diamond_reflections.csv"

PULSES = {
    "nexus_diamond": [3, 6, 15, 30, 60, 120, 300, 600, 1080],
    "adara_diamond": [3, 6, 15, 30, 60, 120, 300, 600, 1080],
    "nexus_background": [30, 60, 150, 300, 600, 1500, 3000, 6000, 15000],
    "nexus_normalization": [6, 15, 30, 60, 150, 300, 600, 1500, 3000],
}
TOF_TICK_US = {"nexus_diamond": 1.0, "adara_diamond": 0.1}
Q_MAX = 40.0
Q_BIN = 0.02
COMPARE_Q = (1.0, 20.0)
SIGMA_Q = (2.0, 18.0)
PEAK_HALF_WIDTH = 0.12
STRONG_PEAK_MIN_INTENSITY = 2.0
TARGET_R = 0.10  # drawn as a guide: 90 % similar
FILE_LIMIT_BYTES = 8 * 1024 * 1024  # check-added-large-files --maxkb=8192
FILE_LIMIT_MARGIN = 0.95
CORRECTION_R_INCREASE = 0.05


@dataclass
class Sample:
    """A raw sample histogram and the proton charge behind it."""

    counts: np.ndarray
    charge_pc: float


@dataclass
class Trim:
    kind: str
    pulses: int
    bytes: int
    events: int
    artifact: str  # a raw-histogram CSV for samples, a preparer CSV for corrections
    charge_pc: float


# ---------------------------------------------------------------------------------------------
# Reductions
# ---------------------------------------------------------------------------------------------


def histogram_sample(source: Path, kind: str, output_csv: Path) -> Sample:
    """Histogram a sample without corrections, in raw counts, and read back its charge."""
    statistics_csv = output_csv.with_suffix(".statistics.csv")
    if output_csv.exists() and statistics_csv.exists() and output_csv.stat().st_mtime >= source.stat().st_mtime:
        return load_histogram(output_csv)
    option = "--nexus-file" if kind == "nexus_diamond" else "--adara-file"
    subprocess.run(
        [
            sys.executable, "-m", "live_stream_analysis.main", "analyze", option, str(source),
            "--histogram-pixel-geometry-csv", str(GEOMETRY),
            "--histogram-q-max", str(Q_MAX), "--histogram-q-bin-size", str(Q_BIN),
            "--tof-tick-us", str(TOF_TICK_US[kind]), "--normalize-by", "none",
            "--histogram-output-csv", str(output_csv),
            "--statistics-output-csv", str(statistics_csv),
            "--log-level", "ERROR",
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip
    return load_histogram(output_csv)


def load_histogram(output_csv: Path) -> Sample:
    counts = read_csv(output_csv)["I(Q)"]
    charge = float(read_csv(output_csv.with_suffix(".statistics.csv"))["Proton charge (pC)"][-1])
    return Sample(counts=np.rint(counts).astype(np.int64), charge_pc=charge)


def reduce_correction(sources: list[Path], mode: str, output_csv: Path) -> None:
    result = reduce_nexus_files(
        nexus_files=sources, q_min=0.0, q_max=Q_MAX, q_bin_size=Q_BIN, tof_tick_us=1.0,
        mode=mode, peak_window=31, peak_z_threshold=3.0,
    )  # fmt: skip
    write_reduction_csv(result, output_csv)


def corrected(sample: Sample, background_csv: Path, normalization_csv: Path) -> tuple[np.ndarray, np.ndarray]:
    """The analyzer's corrected I(Q) for this sample, background and normalization."""
    args = argparse.Namespace(
        background_subtraction=str(background_csv),
        normalization=str(normalization_csv),
        normalize_by="proton-charge",
        histogram_q_min=0.0,
        histogram_q_max=Q_MAX,
        histogram_q_bin_size=Q_BIN,
        sample_coherent_scatter_length=None,
        sample_total_scatter_length_squared=None,
    )
    state = HistogramRuntimeState()
    state.run_statistics.accumulator.proton_charge_pc = sample.charge_pc
    intensity, error = apply_corrections(sample.counts.tolist(), args, len(sample.counts), runtime_state=state)
    return np.asarray(intensity), np.asarray(error)


# ---------------------------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------------------------

Q_VALUES = (np.arange(int(round(Q_MAX / Q_BIN))) + 0.5) * Q_BIN


def _window(bounds: tuple[float, float]) -> np.ndarray:
    return (Q_VALUES > bounds[0]) & (Q_VALUES < bounds[1])


def peak_centres(intensity: np.ndarray, positions: np.ndarray) -> np.ndarray:
    """Baseline-subtracted centroid of each peak, in a fixed window around its position."""
    centres = []
    for position in positions:
        window = np.abs(Q_VALUES - position) <= PEAK_HALF_WIDTH
        signal = intensity[window] - intensity[window].min()
        centres.append(np.sum(Q_VALUES[window] * signal) / np.sum(signal) if signal.sum() > 0 else np.nan)
    return np.array(centres)


def compare(intensity: np.ndarray, error: np.ndarray, reference: np.ndarray, peaks: np.ndarray) -> dict[str, float]:
    window = _window(COMPARE_Q)
    r = float(np.linalg.norm(intensity[window] - reference[window]) / np.linalg.norm(reference[window]))
    shift = peak_centres(intensity, peaks) - peak_centres(reference, peaks)
    sigma_window = _window(SIGMA_Q) & (intensity != 0.0)
    return {
        "r": r,
        "similarity": 1.0 - r,
        "max_peak_shift": float(np.nanmax(np.abs(shift))),
        "mean_sigma_over_i": float(np.mean(np.abs(error[sigma_window] / intensity[sigma_window]))),
    }


# ---------------------------------------------------------------------------------------------
# The study
# ---------------------------------------------------------------------------------------------


def trim_file(kind: str, pulses: int, path: Path) -> None:
    """Trim one input, unless a trim of that size is already in place."""
    if path.exists():
        return
    if kind == "adara_diamond":
        trim_adara(INPUTS[kind][0], INPUTS[kind][1], path, pulses)
    else:
        trim_nexus(INPUTS[kind], path, pulses)


def make_trims(kind: str, work: Path) -> list[Trim]:
    trims = []
    for pulses in PULSES[kind]:
        path = work / f"{kind}_{pulses}{'.adara' if kind == 'adara_diamond' else '.nxs.h5'}"
        trim_file(kind, pulses, path)
        artifact = work / f"{kind}_{pulses}.csv"
        if kind.endswith("diamond"):
            sample = histogram_sample(path, kind, artifact)
            events, charge = int(sample.counts.sum()), sample.charge_pc
        else:
            if not artifact.exists():
                reduce_correction([path], kind.removeprefix("nexus_"), artifact)
            table = read_csv(artifact)
            events, charge = int(round(float(np.sum(table["I(Q)"])))), float(table["Proton charge (pC)"][0])
        trims.append(Trim(kind, pulses, path.stat().st_size, events, str(artifact), charge))
        print(f"  {kind:20} {pulses:6d} pulses  {path.stat().st_size / 1e6:8.2f} MB  {events:>11,} events", flush=True)
    return trims


def load_sample(trim: Trim) -> Sample:
    return Sample(np.rint(read_csv(trim.artifact)["I(Q)"]).astype(np.int64), trim.charge_pc)


def fit_noise(events: np.ndarray, r: np.ndarray) -> tuple[float, float]:
    """Least-squares fit of R^2 = k^2/N + c^2, returning (k, c)."""
    design = np.column_stack([1.0 / events, np.ones_like(events)])
    (k2, c2), *_ = np.linalg.lstsq(design, r**2, rcond=None)
    return float(np.sqrt(max(k2, 0.0))), float(np.sqrt(max(c2, 0.0)))


def main() -> int:  # noqa: C901
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work-dir", type=Path, required=True, help="Where trimmed files and reductions go.")
    parser.add_argument("--results-dir", type=Path, default=Path(__file__).resolve().parent / "results")
    args = parser.parse_args()
    work, results = args.work_dir, args.results_dir
    work.mkdir(parents=True, exist_ok=True)
    results.mkdir(parents=True, exist_ok=True)

    print("Full-data reference", flush=True)
    reference_background = work / "reference_background.csv"
    reference_normalization = work / "reference_normalization.csv"
    reduce_correction(FULL_BACKGROUND, "background", reference_background)
    reduce_correction([INPUTS["nexus_normalization"]], "normalization", reference_normalization)
    reference_sample = histogram_sample(INPUTS["nexus_diamond"], "nexus_diamond", work / "reference_sample.csv")
    reference, reference_error = corrected(reference_sample, reference_background, reference_normalization)
    # The offline corrections must reproduce what the analyzer itself writes, or every number
    # below measures this script rather than the data.
    online = REPO / "notebooks/output/nexus_cli_iq.csv"
    if online.exists():
        online_intensity = read_csv(online)["I(Q)"]
        mismatch = float(np.max(np.abs(online_intensity - reference)) / np.max(np.abs(reference)))
        print(f"  offline vs analyzer-written corrected I(Q): max relative difference {mismatch:.2e}", flush=True)
        if mismatch > 1e-6:
            raise SystemExit("offline corrections do not reproduce the analyzer; aborting")
    reflections = read_csv(REFLECTIONS)
    peaks = reflections["Q (1/A)"][
        (reflections["rel intensity"] > STRONG_PEAK_MIN_INTENSITY) & (reflections["Q (1/A)"] < 18)
    ]
    print(f"  {int(reference_sample.counts.sum()):,} histogrammed sample events, {len(peaks)} strong peaks", flush=True)

    print("Trimming", flush=True)
    trims = {kind: make_trims(kind, work) for kind in PULSES}

    rows = []
    for kind, kind_trims in trims.items():
        for trim in kind_trims:
            if kind.endswith("diamond"):
                result = corrected(load_sample(trim), reference_background, reference_normalization)
            elif kind == "nexus_background":
                result = corrected(reference_sample, Path(trim.artifact), reference_normalization)
            else:
                result = corrected(reference_sample, reference_background, Path(trim.artifact))
            rows.append({**asdict(trim), **compare(*result, reference, peaks)})
    with (results / "sweep.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=[k for k in rows[0] if k != "artifact"], extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    # Fit each file's noise, and bytes per event for converting between the two.
    fits = {}
    for kind in PULSES:
        kind_rows = [row for row in rows if row["kind"] == kind]
        events = np.array([row["events"] for row in kind_rows], dtype=float)
        sizes = np.array([row["bytes"] for row in kind_rows], dtype=float)
        k, c = fit_noise(events, np.array([row["r"] for row in kind_rows]))
        slope, intercept = np.polyfit(events, sizes, 1)
        fits[kind] = {"k": k, "c": c, "bytes_per_event": float(slope), "overhead_bytes": float(intercept)}
        print(f"  {kind:20} R^2 = ({k:.4g})^2/N + ({c:.4f})^2   bytes = {intercept:,.0f} + {slope:.2f} N", flush=True)

    # Pick the tutorial trims. The combined R is set almost entirely by the sample, so the sample
    # gets as many pulses as the ADARA file can hold under the file limit, the same for both
    # formats so that they carry identical events. Each correction then gets the smallest trim
    # whose noise adds at most CORRECTION_R_INCREASE to the sample's R. Independent noise adds in
    # quadrature, and the two corrections share that allowance equally.
    events_per_pulse = {
        kind: float(np.median([row["events"] / row["pulses"] for row in rows if row["kind"] == kind]))
        for kind in PULSES
    }
    adara = fits["adara_diamond"]
    sample_bytes_per_pulse = adara["bytes_per_event"] * events_per_pulse["adara_diamond"]
    sample_pulses = int((FILE_LIMIT_BYTES * FILE_LIMIT_MARGIN - adara["overhead_bytes"]) / sample_bytes_per_pulse)
    sample_events = sample_pulses * events_per_pulse["nexus_diamond"]
    sample_r = float(np.sqrt(fits["nexus_diamond"]["k"] ** 2 / sample_events + fits["nexus_diamond"]["c"] ** 2))
    allowed_r = sample_r * np.sqrt(((1 + CORRECTION_R_INCREASE) ** 2 - 1) / 2)
    plan = {kind: {"pulses": sample_pulses, "r_alone": sample_r} for kind in ("nexus_diamond", "adara_diamond")}
    for kind in ("nexus_background", "nexus_normalization"):
        kind_rows = sorted((row for row in rows if row["kind"] == kind), key=lambda row: row["pulses"])
        chosen = next((row for row in kind_rows if row["r"] <= allowed_r), kind_rows[-1])
        plan[kind] = {"pulses": chosen["pulses"], "r_alone": chosen["r"]}
    print(f"  sample: {sample_pulses} pulses, R = {sample_r:.4f}; each correction may add up to R = {allowed_r:.4f}")

    # Check the plan with real trimmed files, combined as each notebook combines them.
    print("Checking the plan with real trims", flush=True)
    checked = {}
    for kind, entry in plan.items():
        stem = f"plan_{kind}_{entry['pulses']}"
        path = work / f"{stem}{'.adara' if kind == 'adara_diamond' else '.nxs.h5'}"
        trim_file(kind, entry["pulses"], path)
        artifact = work / f"{stem}.csv"
        if kind.endswith("diamond"):
            checked[kind] = histogram_sample(path, kind, artifact)
        else:
            reduce_correction([path], kind.removeprefix("nexus_"), artifact)
            checked[kind] = artifact
        entry["bytes"] = path.stat().st_size
    for pipeline in ("nexus_diamond", "adara_diamond"):
        result = corrected(checked[pipeline], checked["nexus_background"], checked["nexus_normalization"])
        plan[pipeline]["combined"] = compare(*result, reference, peaks)
        np.savetxt(work / f"plan_{pipeline}_corrected.txt", np.column_stack([Q_VALUES, *result]))

    summary = {
        "fits": fits,
        "events_per_pulse": events_per_pulse,
        "correction_r_increase": CORRECTION_R_INCREASE,
        "file_limit_bytes": FILE_LIMIT_BYTES,
        "plan": plan,
    }
    (results / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    plot(rows, fits, plan, reference, work, results)
    for kind, entry in plan.items():
        combined = entry.get("combined")
        extra = (
            f"  combined R={combined['r']:.3f}, worst peak shift {combined['max_peak_shift']:.4f} 1/A"
            if combined
            else ""
        )
        print(f"  {kind:20} {entry['pulses']:6d} pulses  {entry['bytes'] / 1e6:6.2f} MB{extra}")
    return 0


# ---------------------------------------------------------------------------------------------
# Plots
# ---------------------------------------------------------------------------------------------

LABELS = {
    "nexus_diamond": "Diamond, NeXus (NOM_244114)",
    "adara_diamond": "Diamond, ADARA (244114)",
    "nexus_background": "Empty can, NeXus (NOM_243710)",
    "nexus_normalization": "Vanadium, NeXus (NOM_243712)",
}
COLORS = {"nexus_diamond": "C0", "adara_diamond": "C1", "nexus_background": "C2", "nexus_normalization": "C3"}


def plot(rows: list[dict], fits: dict, plan: dict, reference: np.ndarray, work: Path, results: Path) -> None:
    fig, (ax_r, ax_peak) = plt.subplots(1, 2, figsize=(14, 5.2))
    for kind in PULSES:
        kind_rows = [row for row in rows if row["kind"] == kind]
        size = np.array([row["bytes"] for row in kind_rows]) / 1e6
        events = np.array([row["events"] for row in kind_rows], dtype=float)
        ax_r.loglog(size, [row["r"] for row in kind_rows], "o", color=COLORS[kind], label=LABELS[kind])
        # The vanadium is smoothed and peak-stripped, so its noise is not counting-limited and a
        # 1/sqrt(N) fit would misrepresent it.
        if kind != "nexus_normalization":
            model_events = np.geomspace(events.min(), events.max(), 100)
            model_size = (fits[kind]["overhead_bytes"] + fits[kind]["bytes_per_event"] * model_events) / 1e6
            model_r = np.sqrt(fits[kind]["k"] ** 2 / model_events + fits[kind]["c"] ** 2)
            ax_r.loglog(model_size, model_r, "-", color=COLORS[kind], linewidth=0.8)
        ax_r.plot(
            plan[kind]["bytes"] / 1e6,
            plan[kind]["r_alone"],
            "*",
            color=COLORS[kind],
            markersize=14,
            markeredgecolor="k",
        )
        ax_peak.loglog(
            size, [max(row["max_peak_shift"], 1e-5) for row in kind_rows], "o-", color=COLORS[kind], label=LABELS[kind]
        )
    for ax in (ax_r, ax_peak):
        ax.axvline(FILE_LIMIT_BYTES / 1e6, color="0.4", linestyle=":", label="8 MB file limit")
        ax.set_xlabel("Trimmed file size (MB)")
        ax.grid(True, which="both", alpha=0.3)
    ax_r.axhline(TARGET_R, color="0.4", linestyle="--", label=f"R = {TARGET_R} (90 % similar)")
    ax_r.set(
        ylabel="R, relative RMS difference of corrected I(Q), Q 1-20 1/A",
        title="Each file trimmed alone, others at full size",
    )
    ax_peak.axhline(0.002, color="0.4", linestyle="--", label="0.002 1/A (a tenth of a bin)")
    ax_peak.set(ylabel="Worst strong-peak shift (1/A)", title="Diamond peak positions")
    ax_r.legend(fontsize=8)
    ax_peak.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(results / "similarity_vs_size.png", dpi=110)

    fig, axes = plt.subplots(2, 1, figsize=(13, 7.5), sharex=False)
    for ax, (low, high) in zip(axes, [(1.0, 20.0), (6.5, 12.5)]):
        window = (Q_VALUES > low) & (Q_VALUES < high)
        ax.plot(Q_VALUES[window], reference[window], color="k", linewidth=1.0, label="Full data")
        for pipeline in ("nexus_diamond", "adara_diamond"):
            q, intensity, _ = np.loadtxt(work / f"plan_{pipeline}_corrected.txt", unpack=True)
            source = "NeXus" if pipeline == "nexus_diamond" else "ADARA"
            label = f"Trimmed, {source} sample: R = {plan[pipeline]['combined']['r']:.3f}"
            ax.plot(q[window], intensity[window], color=COLORS[pipeline], linewidth=0.8, alpha=0.85, label=label)
        ax.set(xlim=(low, high), xlabel="Q (1/A)", ylabel="Corrected I(Q)")
        ax.legend(fontsize=8)
    axes[0].set_title("Planned tutorial dataset against the full data")
    fig.tight_layout()
    fig.savefig(results / "planned_vs_full.png", dpi=110)


if __name__ == "__main__":
    raise SystemExit(main())
