"""Localhost operator control plane. Virtual research only; no signing or promotion."""

from .control import command, events, initialize, recover, status, tick

__all__ = ["command", "events", "initialize", "main", "recover", "status", "tick"]


def __getattr__(name: str):
    # Importing runtime here would make `python -m memecoin_operator.runtime` execute twice.
    if name == "main":
        from .runtime import main

        return main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
