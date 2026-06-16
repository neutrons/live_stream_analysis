from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("live_stream_analysis")
except PackageNotFoundError:
    # Fallback for source-tree execution without an installed distribution.
    __version__ = "0.2.0"
