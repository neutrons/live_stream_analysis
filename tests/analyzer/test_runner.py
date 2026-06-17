from __future__ import annotations

import argparse

from live_stream_analysis.analyzer.runner import create_source_runner


def test_create_source_runner_returns_nexus_runner_for_nexus_args():
    args = argparse.Namespace(nexus_file=["sample.nxs.h5"])

    runner = create_source_runner(args)

    assert runner.__class__.__name__ == "_NexusRunner"


def test_create_source_runner_returns_adara_runner_without_nexus_args():
    args = argparse.Namespace(nexus_file=None)

    runner = create_source_runner(args)

    assert runner.__class__.__name__ == "_AdaraRunner"
