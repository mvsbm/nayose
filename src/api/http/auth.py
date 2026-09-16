"""Authentication and authorization policy for the HTTP boundary."""

from __future__ import annotations

import secrets
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException, Request

Scope = Literal["read", "review", "admin"]
_ALL_SCOPES = frozenset(("read", "review", "admin"))
_AUTH_CONTEXT: ContextVar[AuthContext | None] = ContextVar("nayose_auth_context", default=None)


@dataclass(frozen=True, slots=True)
class AuthContext:
    subject: str
    scopes: frozenset[str]
    authenticated: bool
    method: str

    def permits(self, scope: Scope) -> bool:
        return scope in self.scopes


class AuthPolicy:
    """Authenticate local bearer tokens or a configured trusted proxy identity."""

    def __init__(
        self,
        mode: str,
        token: str,
        default_subject: str,
        subject_header: str = "X-Authenticated-User",
        scopes_header: str = "X-Authenticated-Scopes",
        review_token: str = "",
        admin_token: str = "",
    ):
        self.mode = mode
        legacy_scopes = _ALL_SCOPES if not (review_token or admin_token) else frozenset(("read",))
        self.credentials: tuple[tuple[str, frozenset[str], str], ...] = tuple(
            item
            for item in (
                (token, legacy_scopes, default_subject),
                (review_token, frozenset(("read", "review")), "reviewer"),
                (admin_token, _ALL_SCOPES, "administrator"),
            )
            if item[0]
        )
        self.default_subject = default_subject
        self.subject_header = subject_header
        self.scopes_header = scopes_header

    def authenticate(self, request: Request) -> AuthContext | None:
        if self.mode == "trusted_proxy":
            subject = request.headers.get(self.subject_header, "").strip()
            scopes = self._scopes(request.headers.get(self.scopes_header, ""))
            if not subject or not scopes:
                return None
            return AuthContext(subject[:128], scopes, True, "trusted_proxy")
        supplied = request.headers.get("authorization", "")
        for token, scopes, subject in self.credentials:
            expected = f"Bearer {token}"
            if secrets.compare_digest(supplied, expected):
                return AuthContext(subject, scopes, True, "bearer")
        if self.credentials:
            return None
        return AuthContext(self.default_subject, _ALL_SCOPES, False, "loopback")

    @staticmethod
    def _scopes(value: str) -> frozenset[str]:
        scopes = frozenset(item for item in value.split() if item in _ALL_SCOPES)
        return scopes if "read" in scopes else frozenset()


def bind_auth(context: AuthContext | None) -> None:
    _AUTH_CONTEXT.set(context)


def clear_auth() -> None:
    _AUTH_CONTEXT.set(None)


def current_auth() -> AuthContext | None:
    return _AUTH_CONTEXT.get()


def require_scope(scope: Scope):
    def dependency(request: Request) -> None:
        context = getattr(request.state, "auth", None)
        if not isinstance(context, AuthContext) or not context.permits(scope):
            raise HTTPException(403, "forbidden")

    return dependency
