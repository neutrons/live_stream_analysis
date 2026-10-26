"""Run the analyzer from a Jupyter notebook, with its live plot shown inline.

The analyzer runs in a child process rather than a thread. A live stream reconnects
indefinitely and a thread cannot be interrupted, so only a process can be stopped from the
notebook; it also keeps the analyzer's log output out of whichever cell happens to be running.

The browser live plot polls its own HTTP server, independently of the notebook kernel, so an
inline frame pointed at that server keeps updating while other cells run.

Typical use::

    from live_stream_analysis.notebook import start_analysis

    run = start_analysis(
        nexus_file=["NOM_243708.nxs.h5"],
        histogram_pixel_geometry_csv="pixel_geometry.csv",
        histogram_output_csv="iq.csv",
    )
    run.show()  # live plot in the cell output
    run.wait()  # block until the analysis finishes
    run.stop()  # shut down the plot server
"""

from __future__ import annotations

import argparse
import atexit
import contextlib
import csv
import difflib
import io
import json
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

from .main import build_parser

LIVE_PLOT_MODES = ("inline", "browser", None)
CLI_PROGRAM_NAMES = ("live_stream_analysis", "live-stream-analysis-cli")


def _analyze_parser() -> argparse.ArgumentParser:
    parser = build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices["analyze"]
    raise RuntimeError("analyze subcommand is not registered")


def _parse_analyze_argv(argv: list[str]) -> argparse.Namespace:
    """Parse ``analyze`` arguments, raising ValueError instead of exiting the kernel."""
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr):
            args = build_parser().parse_args(argv)
    except SystemExit as exc:
        message = stderr.getvalue().strip().splitlines()
        raise ValueError(message[-1] if message else f"invalid analyze arguments: {argv}") from exc
    if args.enable_intersect and not args.intersect_config:
        raise ValueError("enable_intersect requires intersect_config")
    return args


def analyze_argv(**options) -> list[str]:
    """Translate keyword options into ``analyze`` command-line arguments.

    Each keyword is the CLI flag with dashes as underscores, so ``--histogram-q-max 30`` is
    ``histogram_q_max=30``. Flags take True, repeatable options such as ``nexus_file`` take a
    list, and ``adara_stream`` takes a (host, port) pair. None and False are left out. The
    result is parsed by the real CLI parser, so defaults and validation match the CLI exactly.
    """
    actions = {action.dest: action for action in _analyze_parser()._actions if action.option_strings}
    actions.pop("help", None)
    argv = ["analyze"]
    for name, value in options.items():
        action = actions.get(name)
        if action is None:
            suggestion = difflib.get_close_matches(name, actions, n=1)
            hint = f"; did you mean {suggestion[0]!r}?" if suggestion else ""
            raise TypeError(f"unknown analyze option {name!r}{hint}")
        if value is None or value is False:
            continue
        flag = max(action.option_strings, key=len)
        if action.nargs == 0:
            if value is not True:
                raise TypeError(f"{name} is a flag; pass True or False, not {value!r}")
            argv.append(flag)
        elif isinstance(action, argparse._AppendAction):
            values = [value] if isinstance(value, (str, Path)) else list(value)
            for item in values:
                argv += [flag, str(item)]
        elif isinstance(action.nargs, int):
            argv += [flag, *(str(item) for item in value)]
        else:
            argv += [flag, str(value)]
    _parse_analyze_argv(argv)
    return argv


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return probe.getsockname()[1]


def _live_plot_url(args: argparse.Namespace) -> str | None:
    if args.live_plot_mode != "browser":
        return None
    # A server bound to every interface is still reached through loopback from this machine.
    host = "127.0.0.1" if args.live_plot_host in ("0.0.0.0", "") else args.live_plot_host
    return f"http://{host}:{args.live_plot_port}/"


class AnalysisRun:
    """A running ``analyze`` child process and, when enabled, its browser live plot."""

    def __init__(self, argv: list[str], *, log_file: str | Path | None = None, cwd: str | Path | None = None):
        args = _parse_analyze_argv(argv)
        self.argv = list(argv)
        self.url = _live_plot_url(args)
        if log_file is None:
            handle, name = tempfile.mkstemp(prefix="live_stream_analysis-", suffix=".log")
            with open(handle, "w", encoding="utf-8"):
                pass
            log_file = name
        self.log_path = Path(log_file).resolve()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("w", encoding="utf-8") as log:
            self._process = subprocess.Popen(
                [sys.executable, "-u", "-m", "live_stream_analysis.main", *self.argv],
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=cwd,
            )
        # Restarting or shutting down the kernel would otherwise orphan the analyzer, which keeps
        # its live-plot port and, on a live stream, runs indefinitely.
        atexit.register(self._terminate)

    @property
    def command(self) -> str:
        """The equivalent shell command, for copying into a terminal."""
        return shlex.join([CLI_PROGRAM_NAMES[0], *self.argv])

    @property
    def returncode(self) -> int | None:
        """Exit code of the analyzer, or None while it is still running."""
        return self._process.poll()

    def plot_state(self) -> dict | None:
        """The live plot's current payload, or None if the plot is off or not reachable."""
        if self.url is None:
            return None
        try:
            with urllib.request.urlopen(self.url + "state", timeout=2) as response:
                return json.load(response)
        except (urllib.error.URLError, OSError, ValueError):
            return None

    def finished(self) -> bool:
        """True once the analysis is done, even if the live plot is being kept open."""
        if self.returncode is not None:
            return True
        state = self.plot_state()
        return bool(state and state.get("closed"))

    def wait_for_plot(self, timeout: float = 120.0) -> None:
        """Block until the live plot server answers."""
        if self.url is None:
            raise RuntimeError("this run has no browser live plot; start it with live_plot='inline' or 'browser'")
        deadline = time.monotonic() + timeout
        while self.plot_state() is None:
            if self.returncode is not None:
                raise RuntimeError(f"analyzer exited with code {self.returncode}:\n{self.log()}")
            if time.monotonic() > deadline:
                raise TimeoutError(f"live plot did not come up at {self.url} within {timeout} s")
            time.sleep(0.5)

    def show(self, height: int = 950) -> None:
        """Display the live plot in the notebook cell output."""
        from IPython.display import IFrame, display

        self.wait_for_plot()
        display(IFrame(self.url, width="100%", height=height))

    def wait(self, timeout: float | None = None, poll_seconds: float = 2.0) -> None:
        """Block until the analysis finishes. The live plot, if any, stays up until stop()."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.finished():
            if deadline is not None and time.monotonic() > deadline:
                raise TimeoutError(f"analysis still running after {timeout} s")
            time.sleep(poll_seconds)
        if self.returncode not in (None, 0):
            raise RuntimeError(f"analyzer exited with code {self.returncode}:\n{self.log()}")

    def wait_for_log(self, text: str, timeout: float | None = None, poll_seconds: float = 2.0) -> None:
        """Block until ``text`` appears in the analyzer log.

        A live stream never finishes on its own, so this is how to wait for one milestone in
        it, such as a completed run.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while text not in self.log_path.read_text(encoding="utf-8", errors="replace"):
            if self.returncode is not None:
                raise RuntimeError(
                    f"analyzer exited with code {self.returncode} before logging {text!r}:\n{self.log()}"
                )
            if deadline is not None and time.monotonic() > deadline:
                raise TimeoutError(f"{text!r} not logged within {timeout} s")
            time.sleep(poll_seconds)

    def stop(self, timeout: float = 15.0) -> int:
        """Interrupt the analyzer as Ctrl-C would and return its exit code (130 when interrupted).

        Outputs already written stay on disk: completed runs, statistics samples, and
        snapshots. A partial run that is still accumulating is not written.
        """
        if self._process.poll() is None:
            self._process.send_signal(signal.SIGINT)
            try:
                self._process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
        return self._process.returncode

    def _terminate(self) -> None:
        if self._process.poll() is None:
            self._process.terminate()

    def log(self, lines: int = 30) -> str:
        """The last ``lines`` lines of the analyzer's combined stdout and stderr."""
        # newline="" keeps carriage returns, which universal-newline reading turns into line breaks.
        with self.log_path.open(encoding="utf-8", errors="replace", newline="") as handle:
            text = handle.read()
        # Progress bars redraw with carriage returns; keep only what each line finally showed.
        shown = [line.rsplit("\r", 1)[-1] for line in text.split("\n")]
        return "\n".join([line for line in shown if line][-lines:])

    def __repr__(self) -> str:
        if self.returncode is None:
            status = "finished, plot open" if self.finished() else "running"
        else:
            status = f"exited {self.returncode}"
        return f"<AnalysisRun {status} url={self.url} log={self.log_path}>"


def start_analysis(
    *,
    live_plot: str | None = "inline",
    log_file: str | Path | None = None,
    cwd: str | Path | None = None,
    **options,
) -> AnalysisRun:
    """Start ``analyze`` in the background with keyword options (see :func:`analyze_argv`).

    ``live_plot="inline"`` serves the browser plot for :meth:`AnalysisRun.show`,
    ``"browser"`` also opens it in a browser tab, and None turns it off. Either way the plot
    is kept open after the analysis finishes, until :meth:`AnalysisRun.stop`.
    """
    if live_plot not in LIVE_PLOT_MODES:
        raise ValueError(f"live_plot must be one of {LIVE_PLOT_MODES}, got {live_plot!r}")
    if live_plot is not None:
        options["live_plot_mode"] = "browser"
        options["live_plot_keep_open"] = True
        options["live_plot_no_open_browser"] = live_plot == "inline"
        options.setdefault("live_plot_port", _free_port(options.get("live_plot_host", "127.0.0.1")))
    return AnalysisRun(analyze_argv(**options), log_file=log_file, cwd=cwd)


def run_command(command: str, *, log_file: str | Path | None = None, cwd: str | Path | None = None) -> AnalysisRun:
    """Start an ``analyze`` shell command in the background, exactly as typed in a terminal."""
    # shlex keeps a backslash-continued newline as a token of its own, so join continuations first.
    argv = shlex.split(command.replace("\\\n", " "), comments=True)
    if argv and Path(argv[0]).name in CLI_PROGRAM_NAMES:
        argv = argv[1:]
    if not argv or argv[0] != "analyze":
        raise ValueError("command must be a 'live_stream_analysis analyze ...' invocation")
    return AnalysisRun(argv, log_file=log_file, cwd=cwd)


def read_csv(path: str | Path) -> dict[str, np.ndarray]:
    """Load a CSV as {column name: array}.

    Numeric columns become float arrays with blank cells as NaN; any other column, such as
    the hkl labels of a reflection list, is kept as strings.
    """
    with Path(path).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    header, body = rows[0], rows[1:]
    columns = {}
    for index, name in enumerate(header):
        cells = [row[index] for row in body]
        try:
            columns[name] = np.array([float(cell) if cell else np.nan for cell in cells])
        except ValueError:
            columns[name] = np.array(cells)
    return columns


__all__ = ["AnalysisRun", "analyze_argv", "read_csv", "run_command", "start_analysis"]
