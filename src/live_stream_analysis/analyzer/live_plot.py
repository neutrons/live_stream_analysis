from __future__ import annotations

import argparse
import json
import socketserver
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Protocol
from urllib.parse import urlparse

import matplotlib
import matplotlib.pyplot as plt


class HistogramPlotter(Protocol):
    def update(self, intensity: list[float], error: list[float], relative_uncertainty: list[float]) -> None: ...

    def wait_until_closed(self) -> None: ...

    def close(self) -> None: ...


class _BrowserPlotState:
    def __init__(self, q_values: list[float]):
        self._lock = threading.Lock()
        self._payload = {
            "q_values": q_values,
            "intensity": [0.0] * len(q_values),
            "error": [0.0] * len(q_values),
            "relative_uncertainty": [0.0] * len(q_values),
            "status": {
                "update_count": 0,
                "nonzero_bins": 0,
                "total_counts": 0.0,
                "max_intensity": 0.0,
                "mean_relative_uncertainty": 0.0,
            },
            "closed": False,
        }

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return dict(self._payload)

    def update(self, intensity: list[float], error: list[float], relative_uncertainty: list[float]) -> None:
        nonzero_bins = sum(1 for value in intensity if value != 0.0)
        total_counts = float(sum(intensity))
        max_intensity = max(intensity, default=0.0)
        nonzero_relative_uncertainty = [value for value in relative_uncertainty if value != 0.0]
        mean_relative_uncertainty = (
            sum(nonzero_relative_uncertainty) / len(nonzero_relative_uncertainty)
            if nonzero_relative_uncertainty
            else 0.0
        )
        with self._lock:
            self._payload = {
                "q_values": self._payload["q_values"],
                "intensity": intensity,
                "error": error,
                "relative_uncertainty": relative_uncertainty,
                "status": {
                    "update_count": int(self._payload["status"]["update_count"]) + 1,
                    "nonzero_bins": nonzero_bins,
                    "total_counts": total_counts,
                    "max_intensity": max_intensity,
                    "mean_relative_uncertainty": mean_relative_uncertainty,
                },
                "closed": False,
            }

    def close(self) -> None:
        with self._lock:
            self._payload["closed"] = True


class _BrowserPlotRequestHandler(BaseHTTPRequestHandler):
    server: "_BrowserPlotServer"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            body = self.server.html.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/state":
            body = json.dumps(self.server.state.snapshot()).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, _format: str, *_args) -> None:
        return


class _BrowserPlotServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], state: _BrowserPlotState, html: str):
        self.state = state
        self.html = html
        super().__init__(server_address, _BrowserPlotRequestHandler)


class BrowserHistogramPlotter:
    def __init__(
        self,
        q_min: float,
        q_bin_size: float,
        histogram_bins: int,
        host: str,
        port: int,
        open_browser: bool,
    ):
        q_values = [q_min + (index * q_bin_size) for index in range(histogram_bins)]
        self._state = _BrowserPlotState(q_values)
        self._server = _BrowserPlotServer((host, port), self._state, _browser_plot_html())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.url = f"http://{host}:{self._server.server_port}/"
        print(f"Browser live plot available at {self.url}", file=sys.stderr)
        if open_browser:
            webbrowser.open(self.url)

    def update(self, intensity: list[float], error: list[float], relative_uncertainty: list[float]) -> None:
        self._state.update(intensity, error, relative_uncertainty)

    def wait_until_closed(self) -> None:
        self._thread.join()

    def close(self) -> None:
        self._state.close()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=1.0)


class LiveHistogramPlotter:
    def __init__(self, q_min: float, q_bin_size: float, histogram_bins: int):
        plt.ion()
        self._figure, (self._axis, self._relative_axis) = plt.subplots(2, 1, figsize=(12, 9), sharex=True)
        self._q_values = [q_min + (index * q_bin_size) for index in range(histogram_bins)]
        zeros = [0.0] * histogram_bins
        (self._line,) = self._axis.plot(self._q_values, zeros, color="tab:blue", linewidth=1.5, label="I(Q)")
        self._band = self._axis.fill_between(self._q_values, zeros, zeros, color="tab:blue", alpha=0.2)
        (self._relative_line,) = self._relative_axis.plot(
            self._q_values,
            zeros,
            color="tab:orange",
            linewidth=1.5,
            label="sigma / I(Q)",
        )
        self._axis.set_xlabel("Q")
        self._axis.set_ylabel("I(Q)")
        self._axis.set_title("Live Histogram")
        self._axis.grid(True, alpha=0.3)
        self._axis.legend(loc="best")
        self._relative_axis.set_xlabel("Q")
        self._relative_axis.set_ylabel("sigma / I(Q)")
        self._relative_axis.set_title("Live Relative Uncertainty")
        self._relative_axis.grid(True, alpha=0.3)
        self._relative_axis.legend(loc="best")
        self._figure.tight_layout()
        self._figure.canvas.draw_idle()
        self._figure.canvas.flush_events()
        plt.show(block=False)

    def update(self, intensity: list[float], error: list[float], relative_uncertainty: list[float]) -> None:
        lower = [y_value - err_value for y_value, err_value in zip(intensity, error, strict=True)]
        upper = [y_value + err_value for y_value, err_value in zip(intensity, error, strict=True)]
        self._line.set_ydata(intensity)
        self._band.remove()
        self._band = self._axis.fill_between(self._q_values, lower, upper, color="tab:blue", alpha=0.2)
        self._axis.relim()
        self._axis.autoscale_view()
        self._relative_line.set_ydata(relative_uncertainty)
        self._relative_axis.relim()
        self._relative_axis.autoscale_view()
        self._figure.canvas.draw_idle()
        self._figure.canvas.flush_events()
        plt.pause(0.001)

    def wait_until_closed(self) -> None:
        plt.show()

    def close(self) -> None:
        plt.ioff()


class NullHistogramPlotter:
    def update(self, _intensity: list[float], _error: list[float], _relative_uncertainty: list[float]) -> None:
        return

    def wait_until_closed(self) -> None:
        return

    def close(self) -> None:
        return


def create_live_histogram_plotter(args: argparse.Namespace, histogram_bins: int) -> HistogramPlotter:
    mode = getattr(args, "live_plot_mode", None)
    if mode is None:
        return NullHistogramPlotter()

    if mode == "browser":
        return BrowserHistogramPlotter(
            args.histogram_q_min,
            args.histogram_q_bin_size,
            histogram_bins,
            args.live_plot_host,
            args.live_plot_port,
            not args.live_plot_no_open_browser,
        )

    backend = matplotlib.get_backend().lower()
    if backend == "agg" or backend.endswith(":agg"):
        print("Matplotlib is using a non-interactive backend; disabling live plot.", file=sys.stderr)
        return NullHistogramPlotter()

    return LiveHistogramPlotter(args.histogram_q_min, args.histogram_q_bin_size, histogram_bins)


def compute_relative_uncertainty(intensity: list[float], error: list[float]) -> list[float]:
    relative_uncertainty: list[float] = []
    for y_value, err_value in zip(intensity, error, strict=True):
        if y_value == 0.0:
            relative_uncertainty.append(0.0)
            continue
        relative_uncertainty.append(abs(err_value / y_value))
    return relative_uncertainty


def maybe_update_live_plot(
    plotter: HistogramPlotter,
    hist: list[int] | list[float],
    error: list[float],
    refresh_every: int,
    update_index: int,
    *,
    force: bool = False,
) -> None:
    if not force and update_index % refresh_every != 0:
        return

    intensity = [float(value) for value in hist]
    plotter.update(intensity, error, compute_relative_uncertainty(intensity, error))


def _browser_plot_html() -> str:
    return """<!doctype html>
<html lang=\"en\">
<head>
    <meta charset=\"utf-8\">
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
    <title>Live Histogram</title>
    <script src=\"https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js\"></script>
    <script src=\"https://cdn.jsdelivr.net/npm/chartjs-plugin-zoom@2.0.1/dist/chartjs-plugin-zoom.min.js\"></script>
    <style>
        :root {
            --page-bg: #eef2f5;
            --panel-bg: #ffffff;
            --panel-border: #cfd8e3;
            --banner-bg: linear-gradient(90deg, #1f5f3b 0%, #2f7a4a 52%, #5a9a67 100%);
            --banner-accent: #d7e8b6;
            --text-main: #1f2d3d;
            --text-muted: #5f6f82;
            --crumb-bg: #dfe7ef;
            --metric-bg: #f7fafc;
            --metric-border: #d8e2ec;
            --plot-bg: #e5ecf6;
            --line-primary: #1f77b4;
            --line-secondary: #d95f02;
            --shadow: 0 12px 28px rgba(16, 42, 67, 0.08);
        }

        * { box-sizing: border-box; }
        body {
            margin: 0;
            background: radial-gradient(circle at top, #f8fbfd 0%, var(--page-bg) 42%, #e7edf3 100%);
            color: var(--text-main);
            font-family: Arial, Helvetica, sans-serif;
        }

        a { color: #0f5f9a; text-decoration: none; }
        a:hover { text-decoration: underline; }

        .banner {
            position: relative;
            padding: 18px 28px 22px;
            background: var(--banner-bg);
            color: #fff;
            border-bottom: 4px solid var(--banner-accent);
            box-shadow: 0 8px 24px rgba(13, 42, 69, 0.22);
        }

        .banner::after {
            content: '';
            position: absolute;
            inset: 0;
            background: linear-gradient(120deg, rgba(255,255,255,0.08), transparent 35%, transparent 65%, rgba(255,255,255,0.06));
            pointer-events: none;
        }

        .banner-top {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 16px;
            position: relative;
            z-index: 1;
        }

        .banner-mark {
            display: inline-flex;
            align-items: center;
            gap: 12px;
            font-size: 0.9rem;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            color: rgba(244, 250, 240, 0.9);
        }

        .mark-badge {
            width: 42px;
            height: 42px;
            border-radius: 50%;
            display: grid;
            place-items: center;
            background: rgba(235, 245, 228, 0.18);
            border: 1px solid rgba(235, 245, 228, 0.34);
            font-weight: 700;
            color: #f3f8ee;
        }

        .banner-title {
            margin: 18px 0 4px;
            position: relative;
            z-index: 1;
            font-size: clamp(2rem, 4vw, 2.8rem);
            font-weight: 700;
            letter-spacing: 0.01em;
        }

        .banner-subtitle {
            position: relative;
            z-index: 1;
            margin: 0;
            max-width: 900px;
            color: rgba(243, 248, 238, 0.9);
            font-size: 1rem;
        }

        .breadcrumbs {
            display: flex;
            justify-content: space-between;
            gap: 16px;
            flex-wrap: wrap;
            padding: 12px 28px;
            background: var(--crumb-bg);
            border-bottom: 1px solid #c8d4df;
            color: #31465a;
            font-size: 0.95rem;
        }

        .page {
            max-width: 1760px;
            margin: 0 auto;
            padding: 24px 24px 40px;
        }

        .summary {
            background: var(--panel-bg);
            border: 1px solid var(--panel-border);
            border-radius: 10px;
            box-shadow: var(--shadow);
            padding: 22px;
            margin-bottom: 18px;
        }

        .summary-header {
            display: flex;
            justify-content: space-between;
            gap: 16px;
            align-items: start;
            flex-wrap: wrap;
            margin-bottom: 18px;
        }

        .summary-title {
            margin: 0;
            font-size: 1.45rem;
            font-weight: 700;
        }

        .summary-note {
            margin: 6px 0 0;
            color: var(--text-muted);
        }

        .run-state {
            padding: 10px 14px;
            border-radius: 999px;
            background: #edf6ee;
            color: #2f6b3b;
            border: 1px solid #cfe4d2;
            font-weight: 700;
            white-space: nowrap;
        }

        .metrics {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 12px;
        }

        .metric {
            background: var(--metric-bg);
            border: 1px solid var(--metric-border);
            border-radius: 8px;
            padding: 14px 16px;
        }

        .metric-label {
            display: block;
            margin-bottom: 8px;
            color: var(--text-muted);
            font-size: 0.78rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            font-weight: 700;
        }

        .metric-value {
            display: block;
            font-size: 1.5rem;
            font-weight: 700;
            color: #17324d;
        }

        .layout {
            display: grid;
            grid-template-columns: minmax(0, 3.4fr) minmax(260px, 0.8fr);
            gap: 18px;
            align-items: start;
        }

        .panel {
            background: var(--panel-bg);
            border: 1px solid var(--panel-border);
            border-radius: 10px;
            box-shadow: var(--shadow);
            overflow: hidden;
        }

        .panel-header {
            padding: 14px 18px;
            border-bottom: 1px solid #d9e2eb;
            background: linear-gradient(180deg, #fbfdff 0%, #f1f6fb 100%);
        }

        .panel-kicker {
            display: block;
            color: #6b7f92;
            font-size: 0.76rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            font-weight: 700;
            margin-bottom: 4px;
        }

        .panel-title {
            margin: 0;
            font-size: 1.15rem;
        }

        .panel-copy {
            margin: 4px 0 0;
            color: var(--text-muted);
            font-size: 0.95rem;
        }

        .panel-body {
            padding: 16px 18px 18px;
        }

        .chart-shell {
            background: linear-gradient(180deg, #f9fbfd 0%, #f2f6fb 100%);
            border: 1px solid #dce5ef;
            border-radius: 8px;
            padding: 12px;
            height: 420px;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }

        .chart-toolbar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 12px;
            flex-wrap: wrap;
            margin-bottom: 10px;
        }

        .chart-hint {
            color: var(--text-muted);
            font-size: 0.82rem;
        }

        .chart-actions {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }

        .chart-reset {
            border: 1px solid #b8c7d8;
            background: #f7fafc;
            color: #24415c;
            border-radius: 999px;
            padding: 7px 12px;
            font-size: 0.82rem;
            font-weight: 700;
            cursor: pointer;
        }

        .chart-reset:hover {
            background: #edf3f8;
        }

        canvas {
            width: 100% !important;
            height: 100% !important;
            display: block;
            flex: 1 1 auto;
            min-height: 0;
        }

        .stack {
            display: grid;
            gap: 18px;
        }

        .detail-list {
            display: grid;
            gap: 12px;
        }

        .detail-row {
            padding-bottom: 12px;
            border-bottom: 1px solid #e3eaf1;
        }

        .detail-row:last-child {
            padding-bottom: 0;
            border-bottom: 0;
        }

        .detail-label {
            display: block;
            color: var(--text-muted);
            font-size: 0.78rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            font-weight: 700;
            margin-bottom: 4px;
        }

        .detail-value {
            font-size: 1rem;
            line-height: 1.45;
        }

        .status-live { color: #2f6b3b; }
        .status-complete { color: #7a4b00; }

        @media (max-width: 980px) {
            .layout { grid-template-columns: 1fr; }
            .page { padding: 18px 14px 28px; }
            .banner, .breadcrumbs { padding-left: 16px; padding-right: 16px; }
        }
    </style>
</head>
<body>
    <header class=\"banner\">
        <div class=\"banner-top\">
            <div class=\"banner-mark\">
                <span class=\"mark-badge\">N</span>
                <span>Spallation Neutron Source</span>
            </div>
            <div>Live reduction monitor</div>
        </div>
        <h1 class=\"banner-title\">NOM Live Histogram</h1>
        <p class=\"banner-subtitle\">A browser-based monitoring view styled after the NOM run report, with live I(Q) and relative uncertainty updates from the analyzer.</p>
    </header>

    <div class=\"breadcrumbs\">
        <div>
            <a href=\"#\">home</a> &#8250; <a href=\"#\">nom</a> &#8250; <a href=\"#\">live monitor</a> &#8250; histogram
        </div>
        <div>status: <strong id=\"status\" class=\"status-live\">Waiting for data...</strong></div>
    </div>

    <main class=\"page\">
        <section class=\"summary\">
            <div class=\"summary-header\">
                <div>
                    <h2 class=\"summary-title\">Run Summary</h2>
                    <p class=\"summary-note\">Live histogram telemetry for the current analyzer session.</p>
                </div>
                <div class=\"run-state\" id=\"run-state\">Streaming</div>
            </div>
            <div class=\"metrics\">
                <div class=\"metric\"><span class=\"metric-label\">Plot updates</span><span class=\"metric-value\" id=\"update-count\">0</span></div>
                <div class=\"metric\"><span class=\"metric-label\">Total counts</span><span class=\"metric-value\" id=\"total-counts\">0</span></div>
                <div class=\"metric\"><span class=\"metric-label\">Nonzero bins</span><span class=\"metric-value\" id=\"nonzero-bins\">0</span></div>
                <div class=\"metric\"><span class=\"metric-label\">Peak I(Q)</span><span class=\"metric-value\" id=\"max-intensity\">0</span></div>
                <div class=\"metric\"><span class=\"metric-label\">Mean sigma / I(Q)</span><span class=\"metric-value\" id=\"mean-relative-uncertainty\">0</span></div>
            </div>
        </section>

        <section class=\"layout\">
            <div class=\"stack\">
                <section class=\"panel\">
                    <div class=\"panel-header\">
                        <span class=\"panel-kicker\">MTS Reduction</span>
                        <h3 class=\"panel-title\">Live I(Q)</h3>
                        <p class=\"panel-copy\">Primary histogram intensity across Q bins.</p>
                    </div>
                    <div class=\"panel-body\">
                        <div class=\"chart-shell\">
                            <div class=\"chart-toolbar\">
                                <span class=\"chart-hint\">Use the controls to zoom and pan along Q.</span>
                                <div class=\"chart-actions\">
                                    <button class=\"chart-reset\" type=\"button\" id=\"zoom-in-intensity\">Zoom in</button>
                                    <button class=\"chart-reset\" type=\"button\" id=\"zoom-out-intensity\">Zoom out</button>
                                    <button class=\"chart-reset\" type=\"button\" id=\"pan-left-intensity\">Pan left</button>
                                    <button class=\"chart-reset\" type=\"button\" id=\"pan-right-intensity\">Pan right</button>
                                    <button class=\"chart-reset\" type=\"button\" id=\"reset-intensity\">Reset view</button>
                                </div>
                            </div>
                            <canvas id=\"intensity\"></canvas>
                        </div>
                    </div>
                </section>

                <section class=\"panel\">
                    <div class=\"panel-header\">
                        <span class=\"panel-kicker\">Quality Monitor</span>
                        <h3 class=\"panel-title\">Relative Uncertainty</h3>
                        <p class=\"panel-copy\">Per-bin $\sigma / I(Q)$ trend for the current live histogram.</p>
                    </div>
                    <div class=\"panel-body\">
                        <div class=\"chart-shell\">
                            <div class=\"chart-toolbar\">
                                <span class=\"chart-hint\">Use the controls to zoom and pan along Q.</span>
                                <div class=\"chart-actions\">
                                    <button class=\"chart-reset\" type=\"button\" id=\"zoom-in-relative\">Zoom in</button>
                                    <button class=\"chart-reset\" type=\"button\" id=\"zoom-out-relative\">Zoom out</button>
                                    <button class=\"chart-reset\" type=\"button\" id=\"pan-left-relative\">Pan left</button>
                                    <button class=\"chart-reset\" type=\"button\" id=\"pan-right-relative\">Pan right</button>
                                    <button class=\"chart-reset\" type=\"button\" id=\"reset-relative\">Reset view</button>
                                </div>
                            </div>
                            <canvas id=\"relative\"></canvas>
                        </div>
                    </div>
                </section>
            </div>

            <aside class=\"stack\">
                <section class=\"panel\">
                    <div class=\"panel-header\">
                        <span class=\"panel-kicker\">Session Details</span>
                        <h3 class=\"panel-title\">Monitor State</h3>
                    </div>
                    <div class=\"panel-body detail-list\">
                        <div class=\"detail-row\">
                            <span class=\"detail-label\">Current mode</span>
                            <div class=\"detail-value\">Browser live plot</div>
                        </div>
                        <div class=\"detail-row\">
                            <span class=\"detail-label\">Update cadence</span>
                            <div class=\"detail-value\">500 ms polling from local analyzer state</div>
                        </div>
                        <div class=\"detail-row\">
                            <span class=\"detail-label\">Q coverage</span>
                            <div class=\"detail-value\" id=\"q-range\">Waiting for data...</div>
                        </div>
                        <div class=\"detail-row\">
                            <span class=\"detail-label\">Histogram bins</span>
                            <div class=\"detail-value\" id=\"bin-count\">0</div>
                        </div>
                    </div>
                </section>

                <section class=\"panel\">
                    <div class=\"panel-header\">
                        <span class=\"panel-kicker\">Interpretation</span>
                        <h3 class=\"panel-title\">What To Watch</h3>
                    </div>
                    <div class=\"panel-body detail-list\">
                        <div class=\"detail-row\">
                            <span class=\"detail-label\">Intensity panel</span>
                            <div class=\"detail-value\">Tracks the live corrected histogram shape as events accumulate.</div>
                        </div>
                        <div class=\"detail-row\">
                            <span class=\"detail-label\">Uncertainty panel</span>
                            <div class=\"detail-value\">Highlights sparse or unstable regions where relative error remains elevated.</div>
                        </div>
                        <div class=\"detail-row\">
                            <span class=\"detail-label\">Completion state</span>
                            <div class=\"detail-value\">The banner switches from streaming to complete when the analyzer closes the session.</div>
                        </div>
                    </div>
                </section>
            </aside>
        </section>
    </main>
    <script>
        const plotBackground = getComputedStyle(document.documentElement).getPropertyValue('--plot-bg').trim();
        const primaryLine = getComputedStyle(document.documentElement).getPropertyValue('--line-primary').trim();
        const secondaryLine = getComputedStyle(document.documentElement).getPropertyValue('--line-secondary').trim();

        Chart.register(ChartZoom);

        function integerTickFormatter(value) {
            return Math.trunc(Number(value));
        }

        function toSeries(xValues, yValues) {
            return xValues.map((xValue, index) => ({ x: xValue, y: yValues[index] ?? 0 }));
        }

        function toLogSafeSeries(xValues, yValues) {
            return xValues.map((xValue, index) => ({ x: xValue, y: Math.max(yValues[index] ?? 0, 1e-6) }));
        }

        function computeLogYAxisBounds(series) {
            const yValues = series.map((point) => point.y).filter((value) => Number.isFinite(value) && value > 0);
            if (!yValues.length) {
                return { min: 1e-6, max: 1 };
            }
            const dataMin = Math.min(...yValues);
            const dataMax = Math.max(...yValues);
            const min = Math.max(1e-6, dataMin / 1.35);
            const max = Math.max(min * 1.5, dataMax * 1.08);
            return { min, max };
        }

        function initializeXAxisBounds(chart, qValues) {
            if (!qValues.length || chart.$liveBoundsInitialized) {
                return;
            }
            chart.$liveOriginalXBounds = {
                min: qValues[0],
                max: qValues[qValues.length - 1],
            };
            chart.$liveBoundsInitialized = true;
        }

        function resetXAxisBounds(chart) {
            const bounds = chart.$liveOriginalXBounds;
            if (!bounds) {
                chart.resetZoom();
                return;
            }
            chart.zoomScale('x', bounds, 'default');
        }

        function zoomXAxis(chart, factor) {
            const scale = chart.scales.x;
            const bounds = chart.$liveOriginalXBounds;
            if (!scale || !bounds) {
                return;
            }
            const currentMin = scale.min;
            const currentMax = scale.max;
            const center = (currentMin + currentMax) / 2;
            const halfRange = ((currentMax - currentMin) * factor) / 2;
            const nextMin = Math.max(bounds.min, center - halfRange);
            const nextMax = Math.min(bounds.max, center + halfRange);
            chart.zoomScale('x', { min: nextMin, max: nextMax }, 'default');
        }

        function panXAxis(chart, fraction) {
            const scale = chart.scales.x;
            const bounds = chart.$liveOriginalXBounds;
            if (!scale || !bounds) {
                return;
            }
            const currentMin = scale.min;
            const currentMax = scale.max;
            const range = currentMax - currentMin;
            const delta = range * fraction;
            let nextMin = currentMin + delta;
            let nextMax = currentMax + delta;
            if (nextMin < bounds.min) {
                nextMax += bounds.min - nextMin;
                nextMin = bounds.min;
            }
            if (nextMax > bounds.max) {
                nextMin -= nextMax - bounds.max;
                nextMax = bounds.max;
            }
            chart.zoomScale('x', { min: nextMin, max: nextMax }, 'default');
        }

        function zoomPluginOptions() {
            return {
                limits: {
                    x: { min: 'original', max: 'original' },
                },
                pan: {
                    enabled: true,
                    mode: 'x',
                    modifierKey: 'shift',
                },
                zoom: {
                    wheel: { enabled: false },
                    pinch: { enabled: false },
                    drag: {
                        enabled: true,
                        backgroundColor: 'rgba(31, 95, 59, 0.12)',
                        borderColor: 'rgba(31, 95, 59, 0.35)',
                        borderWidth: 1,
                    },
                    mode: 'x',
                },
            };
        }

        const intensityChart = new Chart(document.getElementById('intensity'), {
            type: 'line',
            data: {
                datasets: [{
                    label: 'I(Q)',
                    data: [],
                    borderColor: primaryLine,
                    backgroundColor: 'rgba(31, 119, 180, 0.12)',
                    pointRadius: 0,
                    borderWidth: 1.8,
                    tension: 0.08,
                    fill: false,
                }],
            },
            options: {
                animation: false,
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { labels: { color: '#29435c', boxWidth: 14 } },
                    zoom: zoomPluginOptions(),
                },
                scales: {
                    x: {
                        type: 'linear',
                        title: { display: true, text: 'Q', color: '#29435c', font: { weight: '700' } },
                        ticks: { color: '#4f6478', maxTicksLimit: 10, callback: integerTickFormatter },
                        grid: { color: 'rgba(120, 144, 168, 0.18)' },
                    },
                    y: {
                        type: 'logarithmic',
                        title: { display: true, text: 'I(Q)', color: '#29435c', font: { weight: '700' } },
                        ticks: { color: '#4f6478' },
                        grid: { color: 'rgba(120, 144, 168, 0.18)' },
                    },
                },
                layout: { padding: { left: 6, right: 10, top: 8, bottom: 4 } },
            }
        });
        const relativeChart = new Chart(document.getElementById('relative'), {
            type: 'line',
            data: {
                datasets: [{
                    label: 'sigma / I(Q)',
                    data: [],
                    borderColor: secondaryLine,
                    backgroundColor: 'rgba(217, 95, 2, 0.12)',
                    pointRadius: 0,
                    borderWidth: 1.8,
                    tension: 0.08,
                    fill: false,
                }],
            },
            options: {
                animation: false,
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { labels: { color: '#29435c', boxWidth: 14 } },
                    zoom: zoomPluginOptions(),
                },
                scales: {
                    x: {
                        type: 'linear',
                        title: { display: true, text: 'Q', color: '#29435c', font: { weight: '700' } },
                        ticks: { color: '#4f6478', maxTicksLimit: 10, callback: integerTickFormatter },
                        grid: { color: 'rgba(120, 144, 168, 0.18)' },
                    },
                    y: {
                        title: { display: true, text: 'sigma / I(Q)', color: '#29435c', font: { weight: '700' } },
                        ticks: { color: '#4f6478' },
                        grid: { color: 'rgba(120, 144, 168, 0.18)' },
                    },
                },
                layout: { padding: { left: 6, right: 10, top: 8, bottom: 4 } },
            }
        });

        intensityChart.canvas.parentNode.style.background = plotBackground;
        relativeChart.canvas.parentNode.style.background = plotBackground;

        let interactionDepth = 0;

        function beginChartInteraction() {
            interactionDepth += 1;
        }

        function endChartInteraction() {
            interactionDepth = Math.max(0, interactionDepth - 1);
        }

        function registerInteractionGuards(chart) {
            const canvas = chart.canvas;
            canvas.addEventListener('pointerdown', beginChartInteraction);
            canvas.addEventListener('pointerup', endChartInteraction);
            canvas.addEventListener('pointercancel', endChartInteraction);
            canvas.addEventListener('pointerleave', endChartInteraction);
            canvas.addEventListener('wheel', beginChartInteraction, { passive: true });
            canvas.addEventListener('wheel', () => {
                window.setTimeout(endChartInteraction, 250);
            }, { passive: true });
        }

        registerInteractionGuards(intensityChart);
        registerInteractionGuards(relativeChart);

        document.getElementById('zoom-in-intensity').addEventListener('click', () => zoomXAxis(intensityChart, 0.5));
        document.getElementById('zoom-out-intensity').addEventListener('click', () => zoomXAxis(intensityChart, 2));
        document.getElementById('pan-left-intensity').addEventListener('click', () => panXAxis(intensityChart, -0.2));
        document.getElementById('pan-right-intensity').addEventListener('click', () => panXAxis(intensityChart, 0.2));
        document.getElementById('reset-intensity').addEventListener('click', () => resetXAxisBounds(intensityChart));
        document.getElementById('zoom-in-relative').addEventListener('click', () => zoomXAxis(relativeChart, 0.5));
        document.getElementById('zoom-out-relative').addEventListener('click', () => zoomXAxis(relativeChart, 2));
        document.getElementById('pan-left-relative').addEventListener('click', () => panXAxis(relativeChart, -0.2));
        document.getElementById('pan-right-relative').addEventListener('click', () => panXAxis(relativeChart, 0.2));
        document.getElementById('reset-relative').addEventListener('click', () => resetXAxisBounds(relativeChart));

        function formatRange(values) {
            if (!values.length) {
                return 'Waiting for data...';
            }
            const first = values[0];
            const last = values[values.length - 1];
            return `${first.toFixed(2)} to ${last.toFixed(2)}`;
        }

        async function refresh() {
            if (interactionDepth > 0) {
                return;
            }
            const response = await fetch('/state', { cache: 'no-store' });
            const payload = await response.json();
            const status = payload.status;
            const intensitySeries = toLogSafeSeries(payload.q_values, payload.intensity);
            const relativeSeries = toSeries(payload.q_values, payload.relative_uncertainty);
            const intensityBounds = computeLogYAxisBounds(intensitySeries);
            initializeXAxisBounds(intensityChart, payload.q_values);
            intensityChart.data.datasets[0].data = intensitySeries;
            intensityChart.options.scales.y.min = intensityBounds.min;
            intensityChart.options.scales.y.max = intensityBounds.max;
            intensityChart.update('none');
            initializeXAxisBounds(relativeChart, payload.q_values);
            relativeChart.data.datasets[0].data = relativeSeries;
            relativeChart.update('none');
            const statusElement = document.getElementById('status');
            const runStateElement = document.getElementById('run-state');
            const isClosed = payload.closed;
            statusElement.textContent = isClosed ? 'Analysis complete' : 'Streaming updates';
            statusElement.className = isClosed ? 'status-complete' : 'status-live';
            runStateElement.textContent = isClosed ? 'Complete' : 'Streaming';
            runStateElement.style.background = isClosed ? '#fff4e5' : '#edf6ee';
            runStateElement.style.borderColor = isClosed ? '#f0d3a6' : '#cfe4d2';
            runStateElement.style.color = isClosed ? '#8a5a00' : '#2f6b3b';
            document.getElementById('update-count').textContent = status.update_count.toLocaleString();
            document.getElementById('total-counts').textContent = status.total_counts.toLocaleString(undefined, { maximumFractionDigits: 0 });
            document.getElementById('nonzero-bins').textContent = status.nonzero_bins.toLocaleString();
            document.getElementById('max-intensity').textContent = status.max_intensity.toLocaleString(undefined, { maximumFractionDigits: 2 });
            document.getElementById('mean-relative-uncertainty').textContent = status.mean_relative_uncertainty.toLocaleString(undefined, { maximumFractionDigits: 4 });
            document.getElementById('q-range').textContent = formatRange(payload.q_values);
            document.getElementById('bin-count').textContent = payload.q_values.length.toLocaleString();
        }

        refresh();
        setInterval(refresh, 500);
    </script>
</body>
</html>
"""
