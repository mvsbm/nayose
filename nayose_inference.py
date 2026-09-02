"""
Nayose Inference Engine — Load trained model and run entity matching.

Design:
- Facade Pattern: Single entry point for complex subsystem
- SRP: Each class has one responsibility
- Configuration Object: Frozen dataclass for all settings
"""

from __future__ import annotations

import re
import unicodedata as ud
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from scipy.sparse import load_npz
from sklearn.neighbors import NearestNeighbors

# ─── Configuration ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class InferenceConfig:
    """Immutable configuration for inference."""

    model_dir: Path = Path("models")
    data_dir: Path = Path("data")
    sparse_top_k: int = 16
    exact_block_cap: int = 50
    candidate_cap: int = 50
    pair_auto_threshold: float = 0.105
    entity_auto_threshold: float = 0.016
    entity_review_threshold: float = 0.016


# ─── Normalization (SRP: one function per field) ──────────────────────────────

PUNCT_RE = re.compile(r"[\s\u3000\-‐‑‒–—―ーｰ・･,，.．/／\\()（）\[\]【】]+")
DIGITS_RE = re.compile(r"\D+")
OLD_KANJI = str.maketrans(
    {"髙": "高", "﨑": "崎", "邉": "辺", "邊": "辺", "濱": "浜", "澤": "沢", "齋": "斎", "國": "国"}
)
LEGAL_FORMS = ["株式会社", "有限会社", "合同会社", "（株）", "(株)", "㈱"]


def _compact(s: str) -> str:
    return PUNCT_RE.sub("", ud.normalize("NFKC", s)).lower()


def normalize_name(raw: str) -> str:
    v = _compact(raw).translate(OLD_KANJI)
    for f in sorted(LEGAL_FORMS, key=len, reverse=True):
        v = v.replace(_compact(f), "")
    return v.strip()


def normalize_kana(raw: str) -> str:
    return _compact(raw)


def normalize_phone(raw: str) -> str:
    d = DIGITS_RE.sub("", ud.normalize("NFKC", raw))
    return ("0" + d[2:]) if d.startswith("81") and len(d) > 10 else d


def normalize_postal(raw: str) -> str:
    return DIGITS_RE.sub("", ud.normalize("NFKC", raw)).zfill(7)[:7]


def normalize_address(raw: str) -> str:
    v = _compact(raw)
    for old, new in {"丁目": "-", "番地": "-", "番": "-", "号": ""}.items():
        v = v.replace(old, new)
    return re.sub(r"-+", "-", v).strip("-")


def normalize_email(raw: str) -> str:
    return _compact(raw)


def normalize_dob(raw: str) -> str:
    d = DIGITS_RE.sub("", ud.normalize("NFKC", raw))
    return f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 else d


def normalize_record(rec: dict[str, Any]) -> dict[str, str]:
    """Normalize a raw record into all required views."""
    name = str(rec.get("name_kanji", "")).strip()
    kana = str(rec.get("name_kana", "")).strip()
    n_name = normalize_name(name)
    n_kana = normalize_kana(kana)
    n_phone = normalize_phone(str(rec.get("phone", "")).strip())
    n_postal = normalize_postal(str(rec.get("postal_code", "")).strip())
    n_address = normalize_address(str(rec.get("address", "")).strip())
    n_email = normalize_email(str(rec.get("email", "")).strip())
    n_dob = normalize_dob(str(rec.get("date_birth", "")).strip())

    return {
        "party_type": _compact(str(rec.get("party_type", "person"))),
        "n_name_loose": n_name,
        "n_name_signature": n_name[:4] if len(n_name) >= 4 else n_name,
        "n_kana": n_kana,
        "n_romaji": _compact(str(rec.get("name_romaji", "")).strip()),
        "n_dob": n_dob,
        "n_postal": n_postal,
        "n_address": n_address,
        "n_phone": n_phone,
        "n_phone_suffix8": n_phone[-8:] if len(n_phone) >= 8 else n_phone,
        "n_email": n_email,
        "n_email_local": n_email.split("@")[0] if "@" in n_email else n_email,
        "n_email_domain": n_email.split("@")[1] if "@" in n_email else "",
        "retrieval_text": " ".join(
            filter(None, [n_name, n_kana, n_dob, n_postal, n_address, n_phone, n_email])
        ),
    }


# ─── Pair Features (SRP: pure function) ──────────────────────────────────────

from rapidfuzz.fuzz import ratio


def _fuzzy(a: str, b: str) -> float:
    return ratio(a, b) / 100.0 if a and b else 0.0


PAIR_FEATURE_COLS = [
    "type_exact",
    "name_strict_exact",
    "name_loose_exact",
    "kana_exact",
    "romaji_exact",
    "dob_exact",
    "postal_exact",
    "address_exact",
    "phone_exact",
    "phone_suffix_exact",
    "email_exact",
    "name_similarity",
    "kana_similarity",
    "romaji_similarity",
    "address_similarity",
    "email_local_similarity",
    "email_domain_exact",
    "name_frequency_weight",
    "kana_frequency_weight",
    "phone_frequency_weight",
    "email_frequency_weight",
    "agreement_count",
    "evidence_family_count",
    "both_populated_count",
    "missing_fraction",
    "type_conflict",
    "dob_conflict",
    "postal_conflict",
    "name_dob_evidence",
    "name_address_evidence",
    "sparse_score",
    "dense_score",
    "exact_count",
    "channel_count",
    "retrieval_score",
    "reranker_score",
]


def compute_pair_features(
    query: dict[str, str], candidates: pd.DataFrame, retrieval: pd.DataFrame
) -> pd.DataFrame:
    """Compute all36 pair features between query and each candidate."""
    rows = []
    for i, (_, cand) in enumerate(candidates.iterrows()):
        q, c = query, cand

        # Exact matches
        type_exact = float(
            bool(q["party_type"] and c["party_type"] and q["party_type"] == c["party_type"])
        )
        name_strict_exact = float(
            bool(
                q.get("n_name_strict", "")
                and c.get("n_name_strict", "")
                and q["n_name_strict"] == c["n_name_strict"]
            )
        )
        name_loose_exact = float(
            bool(q["n_name_loose"] and c["n_name_loose"] and q["n_name_loose"] == c["n_name_loose"])
        )
        kana_exact = float(bool(q["n_kana"] and c["n_kana"] and q["n_kana"] == c["n_kana"]))
        romaji_exact = float(
            bool(q["n_romaji"] and c["n_romaji"] and q["n_romaji"] == c["n_romaji"])
        )
        dob_exact = float(bool(q["n_dob"] and c["n_dob"] and q["n_dob"] == c["n_dob"]))
        postal_exact = float(
            bool(q["n_postal"] and c["n_postal"] and q["n_postal"] == c["n_postal"])
        )
        address_exact = float(
            bool(q["n_address"] and c["n_address"] and q["n_address"] == c["n_address"])
        )
        phone_exact = float(bool(q["n_phone"] and c["n_phone"] and q["n_phone"] == c["n_phone"]))
        phone_suffix_exact = float(
            bool(
                q["n_phone_suffix8"]
                and c["n_phone_suffix8"]
                and q["n_phone_suffix8"] == c["n_phone_suffix8"]
            )
        )
        email_exact = float(bool(q["n_email"] and c["n_email"] and q["n_email"] == c["n_email"]))

        # Fuzzy similarities
        name_sim = max(
            _fuzzy(q["n_name_loose"], c["n_name_loose"]),
            _fuzzy(q["n_name_signature"], c["n_name_signature"]),
        )
        kana_sim = _fuzzy(q["n_kana"], c["n_kana"])
        romaji_sim = _fuzzy(q["n_romaji"], c["n_romaji"])
        addr_sim = _fuzzy(q["n_address"], c["n_address"])
        email_local_sim = _fuzzy(q["n_email_local"], c["n_email_local"])
        email_domain_exact = float(
            bool(
                q["n_email_domain"]
                and c["n_email_domain"]
                and q["n_email_domain"] == c["n_email_domain"]
            )
        )

        # Evidence families
        name_evidence = max(name_sim, kana_sim, romaji_sim) >= 0.82
        dob_evidence = dob_exact >= 1
        address_evidence = max(addr_sim, postal_exact) >= 0.82
        phone_evidence = max(phone_exact, phone_suffix_exact) >= 1
        email_evidence = max(email_exact, email_local_sim) >= 0.90
        evidence_family_count = sum(
            [name_evidence, dob_evidence, address_evidence, phone_evidence, email_evidence]
        )

        # Agreement and population counts
        agreement_count = sum(
            [
                name_loose_exact,
                kana_exact,
                romaji_exact,
                dob_exact,
                postal_exact,
                address_exact,
                phone_exact,
                email_exact,
            ]
        )
        identity_fields = [
            "n_name_loose",
            "n_kana",
            "n_romaji",
            "n_dob",
            "n_postal",
            "n_address",
            "n_phone",
            "n_email",
        ]
        both_populated = sum(1 for f in identity_fields if q.get(f, "") and c.get(f, ""))
        missing_fraction = 1.0 - both_populated / len(identity_fields)

        # Conflicts
        type_conflict = float(
            bool(q["party_type"] and c["party_type"] and q["party_type"] != c["party_type"])
        )
        dob_conflict = float(bool(q["n_dob"] and c["n_dob"] and q["n_dob"] != c["n_dob"]))
        postal_conflict = float(
            bool(q["n_postal"] and c["n_postal"] and q["n_postal"] != c["n_postal"])
        )

        # Combined evidence
        best_name = max(name_sim, kana_sim, romaji_sim)
        name_dob_evidence = best_name * dob_exact
        name_address_evidence = best_name * max(addr_sim, postal_exact)

        # Retrieval scores
        ret = retrieval.iloc[i] if i < len(retrieval) else {}
        sparse = float(ret.get("sparse_score", 0))
        dense = float(ret.get("dense_score", 0))
        exact_n = int(ret.get("exact_count", 0))

        rows.append(
            {
                "type_exact": type_exact,
                "name_strict_exact": name_strict_exact,
                "name_loose_exact": name_loose_exact,
                "kana_exact": kana_exact,
                "romaji_exact": romaji_exact,
                "dob_exact": dob_exact,
                "postal_exact": postal_exact,
                "address_exact": address_exact,
                "phone_exact": phone_exact,
                "phone_suffix_exact": phone_suffix_exact,
                "email_exact": email_exact,
                "name_similarity": name_sim,
                "kana_similarity": kana_sim,
                "romaji_similarity": romaji_sim,
                "address_similarity": addr_sim,
                "email_local_similarity": email_local_sim,
                "email_domain_exact": email_domain_exact,
                "name_frequency_weight": 0.0,
                "kana_frequency_weight": 0.0,
                "phone_frequency_weight": 0.0,
                "email_frequency_weight": 0.0,
                "agreement_count": agreement_count,
                "evidence_family_count": evidence_family_count,
                "both_populated_count": both_populated,
                "missing_fraction": missing_fraction,
                "type_conflict": type_conflict,
                "dob_conflict": dob_conflict,
                "postal_conflict": postal_conflict,
                "name_dob_evidence": name_dob_evidence,
                "name_address_evidence": name_address_evidence,
                "sparse_score": sparse,
                "dense_score": dense,
                "exact_count": exact_n,
                "channel_count": int(exact_n > 0) + int(sparse > 0) + int(dense > 0),
                "retrieval_score": max(sparse, dense) + 0.08 * min(exact_n, 3),
                "reranker_score": 0.0,
            }
        )
    return pd.DataFrame(rows)


# ─── Candidate Index (SRP: retrieval only) ───────────────────────────────────


class CandidateIndex:
    """Manages sparse index and block indexes for candidate retrieval."""

    def __init__(
        self,
        reference: pd.DataFrame,
        sparse_matrix,
        vectorizer,
        block_indexes: dict,
        config: InferenceConfig,
    ):
        self.reference = reference
        self.sparse_matrix = sparse_matrix
        self.vectorizer = vectorizer
        self.block_indexes = block_indexes
        self.config = config
        self.sparse_nn = NearestNeighbors(
            n_neighbors=min(config.sparse_top_k + 1, len(reference)),
            metric="cosine",
            algorithm="brute",
        )
        self.sparse_nn.fit(sparse_matrix)

    def retrieve(self, query: dict[str, str]) -> pd.DataFrame:
        """Retrieve candidates using exact blocks + sparse retrieval."""
        states: dict[int, dict] = {}

        # Exact block candidates
        for block_name, groups in self.block_indexes.items():
            key = self._block_key(query, block_name)
            if key and key in groups and len(groups[key]) <= self.config.exact_block_cap:
                for idx in groups[key]:
                    states.setdefault(
                        idx, {"exact_blocks": set(), "sparse_score": 0.0, "dense_score": 0.0}
                    )
                    states[idx]["exact_blocks"].add(block_name)

        # Sparse candidates
        qvec = self.vectorizer.transform([query.get("retrieval_text", "")])
        dists, idxs = self.sparse_nn.kneighbors(
            qvec, n_neighbors=min(self.config.sparse_top_k + 1, len(self.reference))
        )
        for rank, (tgt, score) in enumerate(zip(idxs[0], 1.0 - dists[0]), 1):
            s = states.setdefault(
                int(tgt), {"exact_blocks": set(), "sparse_score": 0.0, "dense_score": 0.0}
            )
            if float(score) > s["sparse_score"]:
                s["sparse_score"] = float(score)

        # Build result
        rows = []
        for idx, s in states.items():
            ec = len(s["exact_blocks"])
            rows.append(
                {
                    "candidate_index": idx,
                    "exact_count": ec,
                    "sparse_score": s["sparse_score"],
                    "dense_score": s["dense_score"],
                    "retrieval_score": max(s["sparse_score"], s["dense_score"]) + 0.08 * min(ec, 3),
                    "channel_count": int(ec > 0) + int(s["sparse_score"] > 0),
                }
            )
        df = pd.DataFrame(rows)
        return (
            df.sort_values("retrieval_score", ascending=False)
            .head(self.config.candidate_cap)
            .reset_index(drop=True)
            if not df.empty
            else df
        )

    def _block_key(self, query: dict, block_name: str) -> str:
        specs = {
            "exact_email": ("n_email",),
            "exact_phone": ("n_phone",),
            "name_dob": ("n_name_loose", "n_dob"),
            "kana_dob": ("n_kana", "n_dob"),
            "signature_postal": ("n_name_signature", "n_postal"),
            "kana_postal": ("n_kana", "n_postal"),
            "name_address": ("n_name_loose", "n_address"),
            "phone_suffix_name": ("n_phone_suffix8", "n_name_loose"),
        }
        cols = specs.get(block_name, ())
        vals = [query.get(c, "") for c in cols]
        return "¦".join(vals) if all(vals) else ""


# ─── Recommendation Result ────────────────────────────────────────────────────


@dataclass
class Recommendation:
    """Immutable result of entity matching."""

    query_id: str
    action: str  # AUTO_LINK, REVIEW, NO_MATCH, INSUFFICIENT_INPUT
    top_nayose_id: str | None
    top_probability: float
    margin: float
    candidates: pd.DataFrame
    diagnostics: dict[str, Any]


# ─── Nayose Engine (Facade) ───────────────────────────────────────────────────


class NayoseEngine:
    """
    Facade for entity matching inference.

    Usage:
        engine = NayoseEngine.load("nayose_output_v2")
        result = engine.recommend({"name_kanji": "山田太郎", "phone": "090-1234-5678"})
    """

    def __init__(self, config: InferenceConfig):
        self.config = config
        self._index: CandidateIndex | None = None
        self._pair_model = None
        self._pair_calibrator = None
        self._reference: pd.DataFrame | None = None
        self._canonical: pd.DataFrame | None = None
        self._record_to_nayose: dict[str, str] = {}

    @classmethod
    def load(cls, model_dir: str | Path = "models", data_dir: str | Path = "data") -> NayoseEngine:
        """Load all artifacts and return a ready-to-use engine."""
        config = InferenceConfig(model_dir=Path(model_dir), data_dir=Path(data_dir))
        engine = cls(config)

        # Load reference data
        engine._reference = pd.read_parquet(config.data_dir / "reference_snapshot.parquet")

        # Load sparse artifacts
        engine._sparse_matrix = load_npz(config.model_dir / "reference_tfidf.npz")
        engine._vectorizer = joblib.load(config.model_dir / "tfidf_vectorizer.joblib")

        # Build block indexes
        engine._block_indexes = engine._build_block_indexes()

        # Create candidate index
        engine._index = CandidateIndex(
            engine._reference,
            engine._sparse_matrix,
            engine._vectorizer,
            engine._block_indexes,
            config,
        )

        # Load models
        engine._pair_model = joblib.load(config.model_dir / "pair_verifier.joblib")
        engine._pair_calibrator = joblib.load(config.model_dir / "pair_calibrator.joblib")

        # Load entity mappings
        engine._canonical = pd.read_parquet(config.model_dir / "entity_index.parquet")
        mapping = pd.read_parquet(config.model_dir / "record_entity_mapping.parquet")
        engine._record_to_nayose = dict(zip(mapping.record_id.astype(str), mapping.nayose_id))

        return engine

    def _build_block_indexes(self) -> dict[str, dict[str, list[int]]]:
        """Build block indexes from reference data."""
        ref = self._reference
        specs = {
            "exact_email": ("email",),
            "exact_phone": ("phone",),
            "name_dob": ("name_kanji", "date_birth"),
            "kana_dob": ("name_kana", "date_birth"),
            "signature_postal": ("name_kanji", "postal_code"),
            "kana_postal": ("name_kana", "postal_code"),
            "name_address": ("name_kanji", "address"),
            "phone_suffix_name": ("phone", "name_kanji"),
        }
        indexes = {}
        for block_name, raw_cols in specs.items():
            groups: dict[str, list[int]] = {}
            for idx, row in ref.iterrows():
                # Normalize the key values
                vals = []
                for col in raw_cols:
                    v = str(row.get(col, ""))
                    if col == "name_kanji":
                        vals.append(normalize_name(v))
                    elif col == "name_kana":
                        vals.append(normalize_kana(v))
                    elif col == "phone":
                        vals.append(
                            normalize_phone(v)[-8:]
                            if "suffix" in block_name
                            else normalize_phone(v)
                        )
                    elif col == "postal_code":
                        vals.append(normalize_postal(v))
                    elif col == "address":
                        vals.append(normalize_address(v))
                    elif col == "date_birth":
                        vals.append(normalize_dob(v))
                    elif col == "email":
                        vals.append(normalize_email(v))
                    else:
                        vals.append(_compact(v))
                key = "¦".join(vals)
                if all(vals):
                    groups.setdefault(key, []).append(idx)
            indexes[block_name] = groups
        return indexes

    def recommend(self, raw_query: dict[str, Any], top_k: int = 8) -> Recommendation:
        """Main inference method."""
        query = normalize_record(raw_query)
        query_id = raw_query.get("query_id", "MANUAL")

        # Check sufficiency
        populated = sum(1 for f in ["n_name_loose", "n_kana", "n_phone", "n_email"] if query.get(f))
        if populated < 1:
            return Recommendation(
                query_id,
                "INSUFFICIENT_INPUT",
                None,
                0.0,
                0.0,
                pd.DataFrame(),
                {"message": "Insufficient input"},
            )

        # Retrieve candidates
        retrieval = self._index.retrieve(query)
        if retrieval.empty:
            return Recommendation(
                query_id, "NO_MATCH", None, 0.0, 0.0, pd.DataFrame(), {"message": "No candidates"}
            )

        # Get candidate records, normalize them, and compute features
        cand_indices = retrieval["candidate_index"].tolist()
        cand_records_raw = self._reference.iloc[cand_indices].reset_index(drop=True)
        # Normalize candidate records to get n_name_loose, n_kana, etc.
        cand_records = pd.DataFrame(
            [normalize_record(row.to_dict()) for _, row in cand_records_raw.iterrows()]
        )
        features = compute_pair_features(query, cand_records, retrieval)

        # Score with pair model
        try:
            pair_probs = self._pair_calibrator.predict_proba(
                self._pair_model.predict(features[PAIR_FEATURE_COLS]).reshape(-1, 1)
            )[:, 1]
        except Exception:
            pair_probs = retrieval["retrieval_score"].values

        # Aggregate to entities
        entity_scores: dict[str, list[float]] = {}
        for idx, prob in zip(cand_indices, pair_probs):
            rid = str(self._reference.iloc[idx].get("record_id", ""))
            nid = self._record_to_nayose.get(rid)
            if nid:
                entity_scores.setdefault(nid, []).append(float(prob))

        entities = sorted(
            [
                {
                    "nayose_id": nid,
                    "probability": np.mean(sorted(ps, reverse=True)[:3]),
                    "support": len(ps),
                }
                for nid, ps in entity_scores.items()
            ],
            key=lambda x: x["probability"],
            reverse=True,
        )

        if not entities:
            return Recommendation(
                query_id, "NO_MATCH", None, 0.0, 0.0, pd.DataFrame(), {"message": "No entities"}
            )

        top = entities[0]
        second = entities[1]["probability"] if len(entities) > 1 else 0.0
        margin = top["probability"] - second

        action = (
            "AUTO_LINK"
            if top["probability"] >= self.config.entity_auto_threshold
            else "REVIEW"
            if top["probability"] >= self.config.entity_review_threshold
            else "NO_MATCH"
        )

        # Build candidate table
        cand_rows = []
        for rank, e in enumerate(entities[:top_k], 1):
            canon = self._canonical[self._canonical.nayose_id == e["nayose_id"]]
            if len(canon) > 0:
                c = canon.iloc[0]
                cand_rows.append(
                    {
                        "rank": rank,
                        "nayose_id": e["nayose_id"],
                        "probability": e["probability"],
                        "name": c.get("name_kanji", ""),
                        "name_kana": c.get("name_kana", ""),
                        "records": c.get("record_count", 0),
                    }
                )

        return Recommendation(
            query_id=query_id,
            action=action,
            top_nayose_id=top["nayose_id"],
            top_probability=top["probability"],
            margin=margin,
            candidates=pd.DataFrame(cand_rows),
            diagnostics={
                "retrieved": len(retrieval),
                "entities": len(entities),
                "top": top["probability"],
                "second": second,
                "margin": margin,
            },
        )


# ─── Convenience ──────────────────────────────────────────────────────────────


def load_engine(model_dir: str = "models", data_dir: str = "data") -> NayoseEngine:
    """Load and return a ready-to-use engine."""
    return NayoseEngine.load(model_dir, data_dir)
