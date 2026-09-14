"""Nayose synthetic data engine.

Data-driven generation of realistic Japanese insurance customer data:
real name frequencies (NVIDIA Nemotron personas), real postal addresses
(Japan Post ken_all), real company names (EDINET), and a correlated,
lineage-tracked dirty-data layer compatible with the training pipeline's contract.
"""

from .config import DIFFICULTY, PROFILES, NoiseConfig, SyntheticConfig
from .engine import (
    build_observations,
    generate_agencies,
    generate_parties,
    generate_queries,
)
from .providers import (
    AddressSource,
    BuildingComposer,
    CompanySource,
    PersonaSource,
)

__all__ = [
    "PROFILES",
    "DIFFICULTY",
    "NoiseConfig",
    "SyntheticConfig",
    "PersonaSource",
    "CompanySource",
    "AddressSource",
    "BuildingComposer",
    "generate_parties",
    "build_observations",
    "generate_queries",
    "generate_agencies",
]
