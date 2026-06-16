"""Command line interface for live_stream_analysis."""

import argparse

from live_stream_analysis import analyzer, preparer


def build_parser() -> argparse.ArgumentParser:
    """Build top-level parser and subcommands."""
    parser = argparse.ArgumentParser(
        prog="live_stream_analysis",
        description="Live stream analysis command line tools.",
    )
    subparsers = parser.add_subparsers(dest="command")

    preparer.add_parser(subparsers)
    analyzer.add_parser(subparsers)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for CLI execution."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "_cmd", None) == "preparer":
        return preparer.run_from_namespace(args)

    if getattr(args, "_cmd", None) == "analyze":
        return analyzer.run_from_namespace(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
