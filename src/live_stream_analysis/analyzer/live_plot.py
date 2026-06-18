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
    def __init__(self, q_bin_size: float, histogram_bins: int, host: str, port: int, open_browser: bool):
        q_values = [(index + 0.5) * q_bin_size for index in range(histogram_bins)]
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
    def __init__(self, q_bin_size: float, histogram_bins: int):
        plt.ion()
        self._figure, (self._axis, self._relative_axis) = plt.subplots(2, 1, figsize=(12, 9), sharex=True)
        self._q_values = [(index + 0.5) * q_bin_size for index in range(histogram_bins)]
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

    return LiveHistogramPlotter(args.histogram_q_bin_size, histogram_bins)


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
    <style>
        body { font-family: Georgia, serif; margin: 0; padding: 24px; background: linear-gradient(180deg, #f7f3ea, #fffdf8); color: #1f1a14; }
        h1 { margin: 0 0 8px; font-size: 2rem; }
        p { margin: 0 0 16px; }
        .panel { background: rgba(255,255,255,0.85); border: 1px solid #d8cdbd; border-radius: 16px; padding: 16px; margin-bottom: 16px; box-shadow: 0 10px 30px rgba(80, 55, 20, 0.08); }
        .status-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 16px; }
        .status-card { background: rgba(247, 243, 234, 0.9); border: 1px solid #e7dccb; border-radius: 12px; padding: 12px 14px; }
        .status-label { display: block; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.08em; color: #7c5d37; margin-bottom: 6px; }
        .status-value { font-size: 1.35rem; font-weight: 700; }
        canvas { width: 100%; height: 320px; }
        #status { font-weight: 600; }
    </style>
</head>
<body>
    <h1>Live Histogram</h1>
    <p id=\"status\">Waiting for data...</p>
    <div class=\"status-grid\">
        <div class=\"status-card\"><span class=\"status-label\">Plot updates</span><span class=\"status-value\" id=\"update-count\">0</span></div>
        <div class=\"status-card\"><span class=\"status-label\">Total counts</span><span class=\"status-value\" id=\"total-counts\">0</span></div>
        <div class=\"status-card\"><span class=\"status-label\">Nonzero bins</span><span class=\"status-value\" id=\"nonzero-bins\">0</span></div>
        <div class=\"status-card\"><span class=\"status-label\">Peak I(Q)</span><span class=\"status-value\" id=\"max-intensity\">0</span></div>
        <div class=\"status-card\"><span class=\"status-label\">Mean sigma / I(Q)</span><span class=\"status-value\" id=\"mean-relative-uncertainty\">0</span></div>
    </div>
    <div class=\"panel\"><canvas id=\"intensity\"></canvas></div>
    <div class=\"panel\"><canvas id=\"relative\"></canvas></div>
    <script>
        const intensityChart = new Chart(document.getElementById('intensity'), {
            type: 'line',
            data: { labels: [], datasets: [{ label: 'I(Q)', data: [], borderColor: '#0f766e', pointRadius: 0, borderWidth: 1.5 }] },
            options: { animation: false, responsive: true, scales: { x: { title: { display: true, text: 'Q' } }, y: { title: { display: true, text: 'I(Q)' } } } }
        });
        const relativeChart = new Chart(document.getElementById('relative'), {
            type: 'line',
            data: { labels: [], datasets: [{ label: 'sigma / I(Q)', data: [], borderColor: '#b45309', pointRadius: 0, borderWidth: 1.5 }] },
            options: { animation: false, responsive: true, scales: { x: { title: { display: true, text: 'Q' } }, y: { title: { display: true, text: 'sigma / I(Q)' } } } }
        });

        async function refresh() {
            const response = await fetch('/state', { cache: 'no-store' });
            const payload = await response.json();
            const status = payload.status;
            intensityChart.data.labels = payload.q_values;
            intensityChart.data.datasets[0].data = payload.intensity;
            intensityChart.update('none');
            relativeChart.data.labels = payload.q_values;
            relativeChart.data.datasets[0].data = payload.relative_uncertainty;
            relativeChart.update('none');
            document.getElementById('status').textContent = payload.closed ? 'Analysis complete.' : 'Streaming updates...';
            document.getElementById('update-count').textContent = status.update_count.toLocaleString();
            document.getElementById('total-counts').textContent = status.total_counts.toLocaleString(undefined, { maximumFractionDigits: 0 });
            document.getElementById('nonzero-bins').textContent = status.nonzero_bins.toLocaleString();
            document.getElementById('max-intensity').textContent = status.max_intensity.toLocaleString(undefined, { maximumFractionDigits: 2 });
            document.getElementById('mean-relative-uncertainty').textContent = status.mean_relative_uncertainty.toLocaleString(undefined, { maximumFractionDigits: 4 });
        }

        refresh();
        setInterval(refresh, 500);
    </script>
</body>
</html>
"""
