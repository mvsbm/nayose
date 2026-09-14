"""Validated process configuration for the dashboard API."""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
EXACT_MODES = ("hybrid", "exact_only", "fuzzy_only")


def _flag(value: str | None) -> bool:
    return (value or "").strip().casefold() in _TRUE_VALUES


def _is_loopback(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise ValueError("NAYOSE_DASH_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValueError("NAYOSE_DASH_PORT must be between 1 and 65535")
    return port


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """One immutable configuration object shared by the application graph."""

    models_dir: Path = Path("models")
    data_dir: Path = Path("data/current")
    data_root: Path = Path("data")
    frontend_dir: Path = Path(__file__).resolve().parents[1] / "frontend"
    bind_host: str = "127.0.0.1"
    port: int = 8000
    api_token: str = ""
    auth_mode: str = "local_bearer"
    trusted_auth_subject_header: str = "X-Authenticated-User"
    trusted_auth_scopes_header: str = "X-Authenticated-Scopes"
    debug_sql: bool = False
    warm_embed: bool = False
    default_reviewer: str = "web"
    device: str = "mps"
    exact_mode: str = "hybrid"
    operational_db: Path | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        if self.auth_mode not in {"local_bearer", "trusted_proxy"}:
            raise ValueError("auth_mode must be local_bearer or trusted_proxy")
        if self.device != "mps":
            raise ValueError("Nayose neural serving is locked to MPS")
        if self.exact_mode not in EXACT_MODES:
            raise ValueError(f"exact_mode must be one of {EXACT_MODES}")
        if self.auth_mode == "trusted_proxy" and _is_loopback(self.bind_host):
            raise ValueError("trusted_proxy authentication must not bind to loopback")
        if (
            self.auth_mode == "local_bearer"
            and not _is_loopback(self.bind_host)
            and not self.api_token
        ):
            raise ValueError("authentication is required when NAYOSE_BIND_HOST is not loopback")
        if not self.trusted_auth_subject_header or not self.trusted_auth_scopes_header:
            raise ValueError("trusted authentication headers must not be empty")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> RuntimeConfig:
        env = os.environ if environ is None else environ
        bind_host = env.get("NAYOSE_BIND_HOST", "127.0.0.1").strip()
        if not bind_host:
            raise ValueError("NAYOSE_BIND_HOST must not be empty")
        api_token = env.get("NAYOSE_API_TOKEN", "").strip()
        auth_mode = env.get("NAYOSE_AUTH_MODE", "local_bearer").strip().casefold()
        if auth_mode not in {"local_bearer", "trusted_proxy"}:
            raise ValueError("NAYOSE_AUTH_MODE must be local_bearer or trusted_proxy")
        if auth_mode == "local_bearer" and not _is_loopback(bind_host) and not api_token:
            raise ValueError("authentication is required when NAYOSE_BIND_HOST is not loopback")
        if auth_mode == "trusted_proxy" and _is_loopback(bind_host):
            raise ValueError("trusted_proxy authentication must not bind to loopback")
        subject_header = env.get("NAYOSE_AUTH_SUBJECT_HEADER", "X-Authenticated-User").strip()
        scopes_header = env.get("NAYOSE_AUTH_SCOPES_HEADER", "X-Authenticated-Scopes").strip()
        if not subject_header or not scopes_header:
            raise ValueError("trusted authentication headers must not be empty")
        data_root = Path(env.get("NAYOSE_DATA_ROOT", "data")).expanduser()
        exact_mode = env.get("NAYOSE_EXACT_MODE", "hybrid").strip()
        if exact_mode not in EXACT_MODES:
            raise ValueError(f"NAYOSE_EXACT_MODE must be one of {EXACT_MODES}")
        return cls(
            models_dir=Path(env.get("NAYOSE_MODELS_DIR", "models")).expanduser(),
            data_dir=Path(env.get("NAYOSE_DATA_DIR", "data/current")).expanduser(),
            data_root=data_root,
            bind_host=bind_host,
            port=_port(env.get("NAYOSE_DASH_PORT", "8000")),
            api_token=api_token,
            auth_mode=auth_mode,
            trusted_auth_subject_header=subject_header,
            trusted_auth_scopes_header=scopes_header,
            debug_sql=_flag(env.get("NAYOSE_DEBUG_SQL")),
            warm_embed=_flag(env.get("NAYOSE_WARM_EMBED")),
            default_reviewer=env.get("NAYOSE_REVIEWER_DEFAULT", "web").strip() or "web",
            device=env.get("NAYOSE_DEVICE", "mps").strip() or "mps",
            exact_mode=exact_mode,
            operational_db=Path(
                env.get("NAYOSE_OPERATIONAL_DB", str(data_root / "operational.sqlite3"))
            ).expanduser(),
        )
