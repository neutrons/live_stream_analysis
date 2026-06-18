from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from intersect_sdk import IntersectClient, IntersectClientCallback, default_intersect_lifecycle_loop

from .config import build_client_config, load_intersect_config
from .service import LiveStreamAnalysisCapability


def run_event_listener(config_path: str | Path) -> int:
    config = load_intersect_config(config_path)
    client = IntersectClient(
        build_client_config(config, LiveStreamAnalysisCapability.intersect_sdk_capability_name),
        event_callback=_print_event_callback,
    )
    try:
        default_intersect_lifecycle_loop(client)
    except KeyboardInterrupt:
        return 0
    return 0


def _print_event_callback(source: str, capability: str, event_name: str, payload: Any) -> IntersectClientCallback | None:
    print(json.dumps({"source": source, "capability": capability, "event": event_name, "payload": payload}))
    return None