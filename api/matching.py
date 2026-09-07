from __future__ import annotations

import copy
import json
import logging
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

import numpy as np
import pandas as pd
from fastapi import HTTPException

from api.engine import load_engine
from api.evidence import compare_fields, sufficient, visible_record
from api.features import PAIR_FEATURE_COLS, compute_pair_features, logit_clip
from api.normalize import normalize_record
from api.retrieval import BundleRetriever
from api.search import EntitySearch

LOGGER = logging.getLogger(__name__)


def empty_result(action: str, reason: str) -> dict[str, Any]:
    return {
        "action": action,
        "reason": reason,
        "top_nayose_id": None,
        "top_probability": 0.0,
        "margin": 0.0,
        "candidates": [],
        "thresholds": None,
        "diagnostics": {},
        "scoring_mode": "not_scored",
    }


class ExplainedMatcher:
    def __init__(self, engine: Any, retriever: Any = None):
        self.engine = engine
        self.retriever = retriever if retriever is not None else engine._index

    @classmethod
    def load(cls, run_dir: Path, data_dir: Path) -> ExplainedMatcher:
        engine = load_engine(model_dir=str(run_dir), data_dir=str(data_dir))
        manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
        return cls(engine, BundleRetriever(engine, manifest, run_dir))

    def recommend(
        self,
        record: dict[str, str],
        stage: Callable[[str], None],
        overrides: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        engine = self.engine
        query = normalize_record(record)
        stage("retrieving")
        retrieval = self.retriever.retrieve(query).reset_index(drop=True)
        if retrieval.empty:
            result = empty_result("NO_MATCH", "no_candidates")
            result["pipeline"] = self._pipeline(record, query, retrieval, result)
            return result
        raw = engine._reference.iloc[retrieval["candidate_index"].tolist()].reset_index(drop=True)
        normalized = pd.DataFrame(
            [normalize_record(visible_record(r)) for r in raw.to_dict("records")]
        )
        stage("scoring")
        features = compute_pair_features(query, normalized, retrieval)
        scores = self._score(features)
        stage("aggregating")
        candidates = self._candidates(record, raw, features, scores, retrieval)
        result = self._decision(candidates, len(retrieval), overrides=overrides)
        result["input_normalized"] = {
            key: query.get(norm, "")
            for key, norm in {
                "name_kanji": "n_name_loose",
                "name_kana": "n_kana",
                "phone": "n_phone",
                "email": "n_email",
                "postal_code": "n_postal",
                "date_birth": "n_dob",
                "address": "n_address",
            }.items()
        }
        result["pipeline"] = self._pipeline(record, query, retrieval, result)
        return result

    def _score(self, features: pd.DataFrame) -> np.ndarray:
        engine = self.engine
        base = np.asarray(engine._pair_model.predict_proba(features[PAIR_FEATURE_COLS])[:, 1])
        inputs = getattr(engine._pair_calibrator, "n_features_in_", 1)
        if inputs == 3:
            calibration = np.column_stack(
                [logit_clip(base), np.zeros(len(base)), np.zeros(len(base))]
            )
        elif inputs == 1:
            calibration = base.reshape(-1, 1)
        else:
            raise ValueError("unsupported calibrator input schema")
        scores = np.asarray(engine._pair_calibrator.predict_proba(calibration)[:, 1])
        if not np.isfinite(scores).all() or ((scores < 0) | (scores > 1)).any():
            raise ValueError("invalid calibrated scores")
        return scores

    def _candidates(
        self,
        record: dict[str, str],
        raw: pd.DataFrame,
        features: pd.DataFrame,
        scores: np.ndarray,
        retrieval: pd.DataFrame,
    ) -> list[dict[str, Any]]:
        engine = self.engine
        groups: dict[str, list[dict[str, Any]]] = {}
        for i, row in enumerate(raw.to_dict("records")):
            entity_id = engine._record_to_nayose.get(str(row["record_id"]))
            if entity_id:
                retrieval_row = retrieval.iloc[i].to_dict()
                groups.setdefault(entity_id, []).append(
                    {
                        "record": visible_record(row),
                        "score": float(scores[i]),
                        "comparisons": compare_fields(record, row),
                        "features": {k: float(v) for k, v in features.iloc[i].items()},
                        "retrieval": {
                            "exact_blocks": [
                                str(block) for block in retrieval_row.get("exact_blocks", [])
                            ],
                            "exact_count": int(retrieval_row.get("exact_count", 0)),
                            "sparse_score": float(retrieval_row.get("sparse_score", 0.0)),
                            "dense_score": float(retrieval_row.get("dense_score", 0.0)),
                            "retrieval_score": float(retrieval_row.get("retrieval_score", 0.0)),
                        },
                    }
                )
        candidates: list[dict[str, Any]] = []
        for entity_id, evidence in groups.items():
            evidence.sort(key=lambda e: (-e["score"], e["record"]["record_id"]))
            canonical = engine._canonical[engine._canonical.nayose_id == entity_id]
            if canonical.empty:
                raise ValueError("entity mapping has no canonical record")
            candidates.append(
                {
                    "nayose_id": entity_id,
                    "name": str(canonical.iloc[0].get("name_kanji", "")),
                    "probability": float(np.mean([e["score"] for e in evidence[:3]])),
                    "support": len(evidence),
                    "evidence": evidence[:3],
                }
            )
        candidates.sort(key=lambda c: (-c["probability"], c["nayose_id"]))
        return candidates

    def _pipeline(
        self,
        record: dict[str, str],
        query: dict[str, str],
        retrieval: pd.DataFrame,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        candidates = result.get("candidates", [])
        top = candidates[0] if candidates else None
        evidence = top.get("evidence", []) if top else []
        first = evidence[0] if evidence else None
        retrieval_info = first.get("retrieval", {}) if first else {}
        comparisons = first.get("comparisons", {}) if first else {}
        statuses = [str(value.get("status", "")) for value in comparisons.values()]
        fields = self._normalized_fields(query)
        branches = {
            "exact": bool(retrieval_info.get("exact_blocks")),
            "sparse": float(retrieval_info.get("sparse_score", 0)) > 0,
            "dense": float(retrieval_info.get("dense_score", 0)) > 0,
        }
        nodes = [
            {
                "id": "input",
                "kind": "input",
                "status": "complete",
                "metrics": {"populated_fields": sum(bool(value) for value in record.values())},
                "details": {"fields": visible_record(record)},
            },
            {
                "id": "normalize",
                "kind": "normalize",
                "status": "complete",
                "metrics": {"fields": len([value for value in fields.values() if value])},
                "details": {"fields": fields},
            },
            {
                "id": "retrieve",
                "kind": "retrieve",
                "status": "complete" if len(retrieval) else "warning",
                "metrics": {
                    "records": len(retrieval),
                    "entities": len(candidates),
                    "backend": str(
                        result.get("diagnostics", {}).get("retrieval_backend", "unknown")
                    ),
                    "mode": str(result.get("diagnostics", {}).get("exact_mode", "unknown")),
                },
                "details": {"candidate": top.get("nayose_id") if top else None},
            },
            *[
                {
                    "id": kind,
                    "kind": kind,
                    "status": "complete" if active else "skipped",
                    "metrics": {"active": active},
                    "details": {
                        "candidate": top.get("nayose_id") if top else None,
                        "exact_blocks": retrieval_info.get("exact_blocks", []),
                        "score": retrieval_info.get(f"{kind}_score", 0.0),
                    },
                }
                for kind, active in branches.items()
            ],
            {
                "id": "candidate",
                "kind": "candidate",
                "status": "complete" if top else "warning",
                "metrics": {"rank": top.get("rank", 0) if top else 0},
                "details": {
                    "id": top.get("nayose_id") if top else None,
                    "name": top.get("name") if top else None,
                },
            },
            {
                "id": "compare",
                "kind": "compare",
                "status": "complete" if first else "skipped",
                "metrics": {
                    "exact": statuses.count("exact"),
                    "normalized": statuses.count("normalized"),
                    "different": statuses.count("different"),
                    "missing": statuses.count("missing"),
                },
                "details": {"fields": comparisons},
            },
            {
                "id": "score",
                "kind": "score",
                "status": "complete" if first else "skipped",
                "metrics": {"records": len(evidence)},
                "details": {"scores": [float(item.get("score", 0)) for item in evidence]},
            },
            {
                "id": "aggregate",
                "kind": "aggregate",
                "status": "complete" if top else "skipped",
                "metrics": {"probability": float(top.get("probability", 0)) if top else 0.0},
                "details": {
                    "method": "mean_top_3",
                    "scores": [float(item.get("score", 0)) for item in evidence],
                },
            },
            {
                "id": "decision",
                "kind": "decision",
                "status": "complete" if result.get("thresholds") else "warning",
                "metrics": {"action": result.get("action", "NO_MATCH")},
                "details": {
                    "reason": result.get("reason", ""),
                    "thresholds": result.get("thresholds"),
                    "margin": result.get("margin", 0.0),
                },
            },
        ]
        return {
            "selected_candidate": top.get("nayose_id") if top else None,
            "nodes": nodes,
            "edges": [
                ["input", "normalize"],
                ["normalize", "retrieve"],
                ["retrieve", "exact"],
                ["retrieve", "sparse"],
                ["retrieve", "dense"],
                ["exact", "candidate"],
                ["sparse", "candidate"],
                ["dense", "candidate"],
                ["candidate", "compare"],
                ["compare", "score"],
                ["score", "aggregate"],
                ["aggregate", "decision"],
            ],
        }

    @staticmethod
    def _normalized_fields(query: dict[str, str]) -> dict[str, str]:
        return {
            field: query.get(normalized, "")
            for field, normalized in {
                "name_kanji": "n_name_loose",
                "name_kana": "n_kana",
                "phone": "n_phone",
                "email": "n_email",
                "postal_code": "n_postal",
                "date_birth": "n_dob",
                "address": "n_address",
            }.items()
        }

    def _decision(
        self,
        candidates: list[dict[str, Any]],
        retrieved: int,
        overrides: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        engine = self.engine
        if not candidates:
            return empty_result("NO_MATCH", "no_entities")
        top = candidates[0]
        auto = float(engine.config.entity_auto_threshold)
        review = float(engine.config.entity_review_threshold)
        source = "manifest"
        # session-only threshold preview (A/B): scores are unchanged, only the
        # decision layer moves; invalid overrides fall back to the manifest
        if overrides:
            cand_auto, cand_review = overrides.get("auto"), overrides.get("review")
            if (
                cand_auto is not None
                and cand_review is not None
                and 0.0 <= cand_review <= cand_auto <= 1.0
            ):
                auto, review = cand_auto, cand_review
                source = "session"
        action, reason = (
            ("AUTO_LINK", "above_auto_threshold")
            if top["probability"] >= auto
            else ("REVIEW", "above_review_threshold")
            if top["probability"] >= review
            else ("NO_MATCH", "below_review_threshold")
        )
        nearest = (
            [dict(c, rank=i) for i, c in enumerate(candidates[:3], 1)]
            if action == "NO_MATCH"
            else None
        )
        return {
            "action": action,
            "reason": reason,
            "top_nayose_id": top["nayose_id"],
            "top_probability": top["probability"],
            "margin": top["probability"]
            - (candidates[1]["probability"] if len(candidates) > 1 else 0),
            "thresholds": {"auto": auto, "review": review},
            "threshold_source": source,
            "nearest": nearest,
            "scoring_mode": "calibrated_pair_mean_top3",
            "diagnostics": {
                "retrieved": retrieved,
                "entities": len(candidates),
                "reranker": False,
                "retrieval_backend": getattr(self.retriever, "active_backend", "unknown"),
                "exact_mode": getattr(self.retriever, "exact_mode", "unknown"),
            },
            "candidates": [
                dict(candidate, rank=i) for i, candidate in enumerate(candidates[:8], 1)
            ],
        }


class RecommendationService:
    def __init__(self, repo: Any, data_dir: Path, loader: Callable = ExplainedMatcher.load):
        self.repo, self.data_dir, self.loader = repo, data_dir, loader
        self._engine_key: tuple[Path, Path] | None = None
        self._engine: ExplainedMatcher | None = None
        self._engine_lock = Lock()
        self._jobs_lock = Lock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="matching")
        self._search_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="entity-search")
        self._jobs: dict[str, dict[str, Any]] = {}
        self._futures: dict[str, Future] = {}
        self._searchers: dict[tuple[Path, Path], EntitySearch] = {}
        self._search_futures: dict[tuple[Path, Path], Future] = {}

    def engine_ready(self, run_id: str) -> bool:
        key = self.repo.resolve(run_id).resolve(), self.data_dir.resolve()
        return self._engine_key == key and self._engine is not None

    def reset_engine(self) -> None:
        """Drop the loaded engine + search caches (reload on next request)."""
        with self._engine_lock:
            self._engine = None
            self._engine_key = None
            self._searchers.clear()
            for future in self._search_futures.values():
                future.cancel()
            self._search_futures.clear()

    def search_entities(
        self, run_id: str, text: str, limit: int = 20
    ) -> list[dict[str, Any]] | None:
        """Semantic entity search over the loaded engine. Returns None when the
        engine is not ready (caller falls back to exact LIKE search) — a search
        must NEVER trigger a multi-minute model load."""
        key = self.repo.resolve(run_id).resolve(), self.data_dir.resolve()
        if not self.engine_ready(run_id):
            return None
        assert self._engine is not None
        searcher = self._searchers.get(key)
        if searcher is None:
            searcher = EntitySearch(self._engine)
            self._searchers[key] = searcher
        future = self._search_futures.get(key)
        if future is None:
            self._search_futures[key] = self._search_pool.submit(searcher.prepare)
            return None
        if not future.done():
            return None
        try:
            future.result()
        except Exception:
            self._search_futures.pop(key, None)
            return None
        return searcher.search(text, top_k=limit)

    def _recommend(
        self,
        key: tuple[Path, Path],
        record: dict[str, str],
        stage: Callable,
        overrides: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        if not sufficient(record):
            return empty_result("INSUFFICIENT_INPUT", "insufficient_input")
        with self._engine_lock:
            if self._engine_key != key:
                stage("loading")
                try:
                    engine = self.loader(*key)
                except (RuntimeError, FileNotFoundError) as exc:
                    raise HTTPException(409, "model_unavailable") from exc
                self._engine, self._engine_key = engine, key
                stage("loading")  # re-stamp with the REAL load time (replaces the pre-load row)
            assert self._engine is not None
            result = self._engine.recommend(record, stage, overrides=overrides)
            if result.get("top_probability", 0) > 0 and result.get("candidates"):
                result["score_context"] = self._score_context(key[0], result["top_probability"])
            return result

    def recommend(
        self, run_id: str, record: dict[str, str], overrides: dict[str, float] | None = None
    ) -> dict[str, Any]:
        key = self.repo.resolve(run_id).resolve(), self.data_dir.resolve()
        return self._recommend(key, record, lambda _: None, overrides=overrides)

    def start(
        self, run_id: str, record: dict[str, str], overrides: dict[str, float] | None = None
    ) -> dict[str, Any]:
        key = self.repo.resolve(run_id).resolve(), self.data_dir.resolve()
        now = time.monotonic()
        with self._jobs_lock:
            expired = [
                k
                for k, j in self._jobs.items()
                if now - j["updated"] > 1800 and j["stage"] in ("complete", "failed")
            ]
            for job_id in expired:
                self._jobs.pop(job_id)
                self._futures.pop(job_id, None)
            if len(self._jobs) >= 32:
                finished = next(
                    (k for k, j in self._jobs.items() if j["stage"] in ("complete", "failed")), None
                )
                if finished is None:
                    raise HTTPException(429, "matching_busy")
                self._jobs.pop(finished)
                self._futures.pop(finished, None)
            job_id = uuid4().hex
            self._jobs[job_id] = {
                "id": job_id,
                "run_id": key[0].name,
                "stage": "queued",
                "started": now,
                "updated": now,
                "timeline": [{"stage": "queued", "elapsed_ms": 0}],
                "result": None,
                "error": None,
            }
            if sufficient(record):
                self._futures[job_id] = self._pool.submit(
                    self._execute, job_id, key, dict(record), dict(overrides) if overrides else None
                )
            else:
                self._jobs[job_id].update(
                    stage="complete",
                    result=empty_result("INSUFFICIENT_INPUT", "insufficient_input"),
                    timeline=[
                        {"stage": "validating", "elapsed_ms": 0},
                        {"stage": "complete", "elapsed_ms": 0},
                    ],
                )
                future: Future = Future()
                future.set_result(None)
                self._futures[job_id] = future
        return self.status(job_id)

    def _update(self, job_id: str, stage: str, **values: Any) -> None:
        with self._jobs_lock:
            job = self._jobs[job_id]
            now = time.monotonic()
            elapsed = round((now - job["started"]) * 1000)
            job.update(stage=stage, updated=now, **values)
            # a stage reported again (e.g. loading stamped before a slow model
            # load) REPLACES its own previous timeline row, so the load time is
            # attributed to loading, not to the following stage
            if job["timeline"][-1]["stage"] == stage:
                job["timeline"][-1]["elapsed_ms"] = elapsed
            else:
                job["timeline"].append({"stage": stage, "elapsed_ms": elapsed})
        LOGGER.info("matching job=%s stage=%s elapsed_ms=%s", job_id, stage, elapsed)

    def _execute(
        self,
        job_id: str,
        key: tuple[Path, Path],
        record: dict[str, str],
        overrides: dict[str, float] | None = None,
    ) -> None:
        try:
            self._update(job_id, "validating")
            result = self._recommend(
                key, record, lambda stage: self._update(job_id, stage), overrides=overrides
            )
            self._update(job_id, "complete", result=result)
        except Exception as exc:
            code = (
                "model_unavailable"
                if isinstance(exc, HTTPException) and exc.status_code == 409
                else "matching_failed"
            )
            LOGGER.warning("matching job=%s error_type=%s", job_id, type(exc).__name__)
            self._update(job_id, "failed", error=code)

    @staticmethod
    def _score_context(run_dir: Path, score: float) -> dict[str, Any]:
        """How much decision load sits at/above this score (counts, not claims)."""
        import duckdb

        con = duckdb.connect()
        try:
            row = con.execute(
                f"SELECT count(*), count(*) FILTER (WHERE pair_probability >= {float(score)}) "
                f"FROM read_parquet('{run_dir}/review_queue.parquet')"
            ).fetchone()
            if row is None:
                return {"at_or_above": 0, "total": 0, "pct": 0.0}
            total, above = int(row[0]), int(row[1])
        except Exception:
            return {"at_or_above": 0, "total": 0, "pct": 0.0}
        finally:
            con.close()
        pct = round(100.0 * above / total, 1) if total else 0.0
        return {"at_or_above": int(above), "total": int(total), "pct": pct}

    def status(self, job_id: str) -> dict[str, Any]:
        with self._jobs_lock:
            if job_id not in self._jobs:
                raise HTTPException(404, "matching_job_expired")
            job = copy.deepcopy(self._jobs[job_id])
        stop = job["updated"] if job["stage"] in ("complete", "failed") else time.monotonic()
        job["elapsed_ms"] = round((stop - job.pop("started")) * 1000)
        job.pop("updated")
        return job

    def wait(self, job_id: str, timeout: float) -> None:
        self._futures[job_id].result(timeout=timeout)

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
