"""Supported embedding profiles shared by training and the admin surface."""

from __future__ import annotations

F2LLM_330M_MODEL_ID = "codefuse-ai/F2LLM-v2-330M"

SUPPORTED_MODEL_FAMILIES = (
    "off",
    "ruri",
    "f2llm_330m",
    "f2llm",
    "f2llm_0.6b",
    "qwen",
)

_NOTEBOOK_MODEL_FAMILY = {"f2llm_330m": "f2llm"}


def notebook_model_family(family: str) -> str:
    """Return the model-family name understood by the legacy notebook driver."""
    try:
        return _NOTEBOOK_MODEL_FAMILY[family]
    except KeyError:
        if family not in SUPPORTED_MODEL_FAMILIES:
            raise ValueError(f"unsupported model family {family!r}") from None
        return family


def embedding_model_id(family: str, explicit: str | None = None) -> str | None:
    """Resolve an explicit model override without silently changing profiles."""
    if explicit:
        return explicit
    if family == "f2llm_330m":
        return F2LLM_330M_MODEL_ID
    return None
