"""Compatibility exports for the canonical embedding configuration view."""

from nayose.config import (
    F2LLM_330M_MODEL_ID,
    F2LLM_MPS_BATCH_SIZE,
    F2LLM_MPS_DEVICE,
    F2LLM_MPS_PROCESS_WORKERS,
    F2LLM_MPS_TORCH_DTYPE,
    SUPPORTED_MODEL_FAMILIES,
    embedding_model_id,
    is_f2llm_model,
    pipeline_model_family,
)

__all__ = [
    "F2LLM_330M_MODEL_ID",
    "F2LLM_MPS_BATCH_SIZE",
    "F2LLM_MPS_DEVICE",
    "F2LLM_MPS_PROCESS_WORKERS",
    "F2LLM_MPS_TORCH_DTYPE",
    "SUPPORTED_MODEL_FAMILIES",
    "embedding_model_id",
    "is_f2llm_model",
    "pipeline_model_family",
]
