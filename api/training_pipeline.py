"""Compatibility entry point for the modular training application."""

from __future__ import annotations

from api.training.application import run_from_environment


def run() -> None:
    run_from_environment()


if __name__ == "__main__":
    run()
