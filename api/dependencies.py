"""FastAPI dependencies shared by route modules."""

from __future__ import annotations

from collections.abc import Callable
from time import perf_counter
from typing import TypeVar

from fastapi import Request

from api.application import ApplicationServices
from api.auth import current_auth

T = TypeVar("T")


def get_services(request: Request) -> ApplicationServices:
    return request.app.state.services


def actor_identity(supplied: str | None, services: ApplicationServices, fallback: str) -> str:
    """Return an honest attribution for local headers or token-only clients."""
    context = current_auth()
    if context is not None and context.authenticated:
        return context.subject.strip()[:64] or fallback
    if services.config.api_token:
        return services.config.default_reviewer
    return (supplied or fallback).strip()[:64] or fallback


def timed_operation(name: str, operation: Callable[[], T], services: ApplicationServices) -> T:
    started = perf_counter()
    try:
        result = operation()
    except Exception:
        services.stats.record(name, perf_counter() - started, error=True)
        raise
    services.stats.record(name, perf_counter() - started)
    return result
