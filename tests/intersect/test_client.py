from __future__ import annotations

import json

from live_stream_analysis.intersect import client


def test_print_event_callback_writes_json_payload(capsys):
    result = client._print_event_callback("ornl.neutrons.nomad.analysis.livestreamanalysis", "nomadanalysis", "histogram_updated", {"q": [1.0]})

    captured = capsys.readouterr()
    assert result is None
    assert json.loads(captured.out) == {
        "source": "ornl.neutrons.nomad.analysis.livestreamanalysis",
        "capability": "nomadanalysis",
        "event": "histogram_updated",
        "payload": {"q": [1.0]},
    }


def test_run_event_listener_builds_client_and_runs_loop(tmp_path, monkeypatch):
    config_path = tmp_path / "intersect.yaml"
    config_path.write_text(
        "\n".join(
            [
                "service:",
                "  name: live-stream-analysis",
                "events:",
                "  histogram: histogram.updated",
                "  run_complete: run.completed",
                "broker:",
                "  protocol: amqp0.9.1",
                "  host: rabbitmq",
                "  port: 5672",
                "  username: guest",
                "  password: guest",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    observed: dict[str, object] = {}

    class FakeClient:
        def __init__(self, config, event_callback):
            observed["config"] = config
            observed["event_callback"] = event_callback

    def fake_loop(client_instance):
        observed["client"] = client_instance

    monkeypatch.setattr(client, "IntersectClient", FakeClient)
    monkeypatch.setattr(client, "default_intersect_lifecycle_loop", fake_loop)

    exit_code = client.run_event_listener(config_path)

    assert exit_code == 0
    assert observed["event_callback"] is client._print_event_callback
    assert isinstance(observed["client"], FakeClient)


def test_run_event_listener_returns_zero_on_keyboard_interrupt(tmp_path, monkeypatch):
    config_path = tmp_path / "intersect.yaml"
    config_path.write_text(
        "\n".join(
            [
                "service:",
                "  name: live-stream-analysis",
                "events:",
                "  histogram: histogram.updated",
                "  run_complete: run.completed",
                "broker:",
                "  protocol: amqp0.9.1",
                "  host: rabbitmq",
                "  port: 5672",
                "  username: guest",
                "  password: guest",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    class FakeClient:
        def __init__(self, config, event_callback):
            self.config = config
            self.event_callback = event_callback

    def fake_loop(_client_instance):
        raise KeyboardInterrupt

    monkeypatch.setattr(client, "IntersectClient", FakeClient)
    monkeypatch.setattr(client, "default_intersect_lifecycle_loop", fake_loop)

    assert client.run_event_listener(config_path) == 0