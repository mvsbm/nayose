import json
from threading import Event
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest


def test_comparisons_distinguish_missing_normalized_and_conflicting():
    from api.domain.evidence import compare_fields

    evidence = compare_fields(
        {"phone": "09012345678", "postal_code": "", "date_birth": "2000-01-01"},
        {"phone": "090-1234-5678", "postal_code": "", "date_birth": "1990-01-01"},
    )
    assert evidence["phone"]["status"] == "normalized"
    assert evidence["postal_code"]["status"] == "missing"
    assert evidence["date_birth"]["status"] == "different"
    assert evidence["phone"]["right_normalized"] == "09012345678"


def fake_engine(broken=False):
    class Model:
        def predict_proba(self, features):
            if broken:
                raise ValueError("broken model")
            return np.array([[0.02, 0.98], [0.03, 0.97]])

    rows = pd.DataFrame(
        [
            {
                "record_id": "R1",
                "name_kanji": "佐藤 太郎",
                "phone": "090-1234-5678",
                "date_birth": "2000-01-01",
            },
            {
                "record_id": "R2",
                "name_kanji": "佐藤 次郎",
                "phone": "090-1234-5678",
                "date_birth": "1990-01-01",
            },
        ]
    )
    return SimpleNamespace(
        _reference=rows,
        _index=SimpleNamespace(
            retrieve=lambda query: pd.DataFrame(
                {
                    "candidate_index": [0, 1],
                    "retrieval_score": [0.8, 0.7],
                    "exact_count": [1, 1],
                    "exact_blocks": [["name_kana"], ["name_kana"]],
                }
            )
        ),
        _pair_model=Model(),
        _pair_calibrator=SimpleNamespace(n_features_in_=1, predict_proba=Model().predict_proba),
        _record_to_nayose={"R1": "N1", "R2": "N2"},
        _canonical=pd.DataFrame(
            [
                {"nayose_id": "N1", "name_kanji": "佐藤 太郎"},
                {"nayose_id": "N2", "name_kanji": "佐藤 次郎"},
            ]
        ),
        config=SimpleNamespace(entity_auto_threshold=0.95, entity_review_threshold=0.6),
    )


def test_safe_identity_anchor_requires_name_and_stable_exact_field():
    from api.domain.features import has_safe_identity_anchor

    assert has_safe_identity_anchor({"name_loose_exact": 1, "dob_exact": 1})
    assert not has_safe_identity_anchor({"name_loose_exact": 1, "phone_exact": 1})
    assert not has_safe_identity_anchor({"name_loose_exact": 0, "email_exact": 1})


def test_matching_returns_actual_support_and_threshold_reason():
    from api.application.matching import ExplainedMatcher

    stages: list[str] = []
    result = ExplainedMatcher(fake_engine()).recommend(
        {
            "name_kanji": "佐藤 太郎",
            "phone": "09012345678",
            "date_birth": "2000-01-01",
        },
        stages.append,
    )
    assert result["action"] == "AUTO_LINK"
    assert result["reason"] == "above_auto_threshold"
    assert result["margin"] == pytest.approx(0.01)
    assert result["thresholds"] == {"auto": 0.95, "review": 0.6}
    support = result["candidates"][0]["evidence"][0]
    assert support["record"]["record_id"] == "R1"
    assert support["comparisons"]["phone"]["status"] == "normalized"
    assert support["features"]["phone_exact"] == 1
    assert support["retrieval"]["sparse_score"] == 0.0
    assert support["retrieval"]["exact_blocks"] == ["name_kana"]
    assert support["retrieval_edges"] == [
        {
            "record_id": "R1",
            "entity_id": "N1",
            "channel": "",
            "view": "exact",
            "rank": 1,
            "score": 1.0,
            "alias": None,
            "confidence": 1.0,
        }
    ]
    assert result["pipeline"]["selected_candidate"] == "N1"
    assert [node["id"] for node in result["pipeline"]["nodes"]] == [
        "input",
        "normalize",
        "retrieve",
        "exact",
        "sparse",
        "dense",
        "candidate",
        "compare",
        "score",
        "aggregate",
        "decision",
    ]
    assert stages == ["retrieving", "scoring", "aggregating"]


def test_minimum_reference_density_allows_safe_no_match():
    from api.application.matching import ExplainedMatcher

    engine = fake_engine()
    engine.config.minimum_reference_density = 1.0

    result = ExplainedMatcher(engine).recommend(
        {
            "name_kanji": "佐藤 太郎",
            "phone": "09012345678",
            "date_birth": "2000-01-01",
        },
        lambda _: None,
    )

    assert result["action"] == "NO_MATCH"
    assert result["reason"] == "reference_density_below_threshold"
    assert result["diagnostics"]["reference_density"] == pytest.approx(0.5)


def test_scoring_failure_is_not_disguised_as_probability():
    from api.application.matching import ExplainedMatcher

    with pytest.raises(ValueError, match="broken model"):
        ExplainedMatcher(fake_engine(broken=True)).recommend({"name_kanji": "佐藤"}, lambda _: None)


def test_inference_config_validates_dense_profile_and_density():
    from api.infrastructure.runtime.engine import InferenceConfig

    assert InferenceConfig(dense_profile="field_aware", minimum_reference_density=0.5)
    with pytest.raises(ValueError, match="dense_profile"):
        InferenceConfig(dense_profile="unknown")
    with pytest.raises(ValueError, match="minimum_reference_density"):
        InferenceConfig(minimum_reference_density=1.1)


def test_engine_load_defaults_to_promoted_aliases():
    import inspect

    from api.infrastructure.runtime.engine import NayoseEngine, load_engine

    assert inspect.signature(NayoseEngine.load).parameters["model_dir"].default == "models/current"
    assert inspect.signature(NayoseEngine.load).parameters["data_dir"].default == "data/current"
    assert inspect.signature(load_engine).parameters["model_dir"].default == "models/current"
    assert inspect.signature(load_engine).parameters["data_dir"].default == "data/current"


def test_engine_refuses_bundles_without_fingerprint_guard(tmp_path):
    from api.infrastructure.runtime.engine import NayoseEngine

    reference = pd.DataFrame({"record_id": ["R1"]})
    config = SimpleNamespace(model_dir=tmp_path)

    with pytest.raises(RuntimeError, match="manifest"):
        NayoseEngine._guard_generation_match(config, reference)

    (tmp_path / "run_manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="data_fingerprint"):
        NayoseEngine._guard_generation_match(config, reference)


def test_query_embedding_encoder_batches_and_reuses_model():
    from api.infrastructure.observability.telemetry import Stats
    from api.infrastructure.runtime.embedding_runtime import QueryEmbeddingEncoder

    calls: list[list[str]] = []

    class FakeModel:
        def encode(self, texts, **kwargs):
            calls.append(list(texts))
            return np.ones((len(texts), 2), dtype=np.float32)

    stats = Stats()
    encoder = QueryEmbeddingEncoder(
        "fake/model",
        device="cpu",
        batch_size=8,
        model_factory=lambda model_id, device: FakeModel(),
        stats=stats,
    )
    first = encoder.encode(["record", "name", "alias"])
    second = encoder.encode(["address"])
    assert first.shape == (3, 2) and second.shape == (1, 2)
    assert calls == [["record", "name", "alias"], ["address"]]
    assert (
        next(row for row in stats.snapshot() if row["endpoint"] == "embedding.encode")["hits"] == 2
    )


def test_process_wide_query_encoder_is_reused_and_rebound(monkeypatch):
    import api.infrastructure.runtime.embedding_runtime as runtime

    created = []
    closed = []

    class FakeEncoder:
        def __init__(self, model_id, *, device, batch_size, stats=None):
            del stats
            created.append((model_id, device, batch_size))

        def encode(self, texts, expected_dimensions=None):
            return np.ones((len(texts), 2), dtype=np.float32)

        def close(self):
            closed.append(True)

    monkeypatch.setattr(runtime, "QueryEmbeddingEncoder", FakeEncoder)
    runtime.close_query_encoder()
    runtime.configure_query_encoder("model-a", device="cpu", batch_size=4)
    runtime.encode_queries(["one"])
    runtime.encode_queries(["two"])
    runtime.configure_query_encoder("model-b", device="cpu", batch_size=4)
    runtime.encode_queries(["three"])
    assert created == [("model-a", "cpu", 4), ("model-b", "cpu", 4)]
    assert len(closed) == 1
    runtime.close_query_encoder()


def test_degraded_dense_index_uses_saved_svd_for_queries(monkeypatch):
    from sklearn.decomposition import TruncatedSVD
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.preprocessing import normalize

    from api.infrastructure.runtime.engine import CandidateIndex, InferenceConfig

    texts = ["alpha beta", "beta gamma", "gamma delta"]
    vectorizer = TfidfVectorizer().fit(texts)
    sparse = vectorizer.transform(texts)
    svd = TruncatedSVD(n_components=2, random_state=7).fit(sparse)
    embeddings = normalize(svd.transform(sparse)).astype(np.float32)

    class DenseIndex:
        def search(self, query, limit):
            return SimpleNamespace(
                keys=np.asarray([[0, 1]], dtype=np.int32),
                distances=np.asarray([[0.1, 0.2]], dtype=np.float32),
            )

    monkeypatch.setenv("NAYOSE_USE_DENSE", "1")
    monkeypatch.setattr(
        "api.infrastructure.runtime.engine.encode_query",
        lambda _: (_ for _ in ()).throw(AssertionError()),
    )
    index = CandidateIndex(
        pd.DataFrame({"record_id": ["R1", "R2", "R3"]}),
        sparse,
        vectorizer,
        {},
        InferenceConfig(),
        embeddings,
        DenseIndex(),
        svd,
    )

    result = index.retrieve({"retrieval_text": "alpha beta"})
    assert result.iloc[0].candidate_index == 0


def test_dense_serving_refuses_missing_ann_artifact(tmp_path):
    from api.infrastructure.runtime.engine import InferenceConfig, _load_dense_artifacts

    np.save(tmp_path / "reference_embeddings.npy", np.ones((2, 3), dtype=np.float32))
    reference = pd.DataFrame({"record_id": ["R1", "R2"]})

    with pytest.raises(RuntimeError, match="requires"):
        _load_dense_artifacts(InferenceConfig(model_dir=tmp_path), reference)


def test_dense_serving_memory_maps_reference_embeddings(tmp_path, monkeypatch):
    import api.infrastructure.runtime.engine as engine

    np.save(tmp_path / "reference_embeddings.f16.npy", np.ones((2, 3), dtype=np.float16))
    (tmp_path / "dense_index.usearch").touch()
    (tmp_path / "run_manifest.json").write_text(
        json.dumps(
            {
                "runtime_artifacts": {
                    "dense_index": {
                        "platform": engine.target_platform(),
                        "usearch_version": engine.usearch_version(),
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(engine, "load_dense_index", lambda *args, **kwargs: object())

    embeddings, _, _ = engine._load_dense_artifacts(
        engine.InferenceConfig(model_dir=tmp_path),
        pd.DataFrame({"record_id": ["R1", "R2"]}),
    )

    assert isinstance(embeddings, np.memmap)
    assert embeddings.dtype == np.float16


def test_svd_bundle_uses_saved_transform_not_neural_encoder(tmp_path, monkeypatch):
    import joblib
    from sklearn.decomposition import TruncatedSVD
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.preprocessing import normalize

    from api.infrastructure.retrieval.bundle_retrieval import BundleRetriever

    texts = ["alpha beta", "beta gamma", "gamma delta"]
    vectorizer = TfidfVectorizer().fit(texts)
    sparse = vectorizer.transform(texts)
    svd = TruncatedSVD(n_components=2, random_state=7).fit(sparse)
    embeddings = normalize(svd.transform(sparse))
    joblib.dump(svd, tmp_path / "dense_svd.joblib")
    reference = pd.DataFrame({"record_id": ["R1", "R2", "R3"]})
    from api.infrastructure.runtime.engine import CandidateIndex, InferenceConfig

    config = InferenceConfig(model_dir=tmp_path)
    index = CandidateIndex(reference, sparse, vectorizer, {}, config, embeddings)
    engine = SimpleNamespace(_index=index)
    retriever = BundleRetriever(
        engine, {"backends": {"dense_index": "tfidf_svd_degraded"}}, tmp_path
    )
    assert retriever.mode == "tfidf_svd"
    vector = retriever.encode("alpha beta")
    assert vector.shape == (1, 2)
    np.testing.assert_allclose(
        vector, normalize(svd.transform(vectorizer.transform(["alpha beta"])))
    )
    monkeypatch.setenv("NAYOSE_EXACT_MODE", "exact_only")
    assert retriever.retrieve({"retrieval_text": "alpha beta"}).empty


def test_normalization_and_pair_features_are_shared_with_engine():
    """The engine re-exports api/* primitives — identity, not a copy."""
    import api.domain.features as features
    import api.domain.normalize as normalize
    import api.infrastructure.runtime.engine as engine

    assert engine.normalize_record is normalize.normalize_record
    assert engine.compute_pair_features is features.compute_pair_features
    assert engine.PAIR_FEATURE_COLS == features.PAIR_FEATURE_COLS
    assert engine.logit_clip is features.logit_clip
    assert engine._resolve_device is not None


def test_normalize_record_retrieval_text_is_labeled_and_model_compatible():
    from api.domain.normalize import normalize_record

    normalized = normalize_record(
        {
            "name_kanji": "佐藤 太郎",
            "name_kana": "サトウ タロウ",
            "name_romaji": "Taro Sato",
            "date_birth": "1980-05-05",
            "postal_code": "100-0001",
            "address": "東京都千代田区1-2-3",
            "phone": "090-1234-5678",
            "email": "taro@example.com",
        }
    )
    assert normalized["retrieval_text"] == (
        "type=person name=佐藤太郎 kana=サトウタロウ romaji=tarosato "
        "dob=1980-05-05 postal=1000001 address=東京都千代田区123 "
        "phone=09012345678 email=taro@examplecom"
    )


def test_normalize_record_field_invariants():
    from api.domain.normalize import (
        normalize_email,
        normalize_kana,
        normalize_phone,
        normalize_postal,
        normalize_record,
    )

    raw = {
        "name_kanji": "髙橋 太郎",
        "name_kana": "タカハシ タロウ",
        "phone": "０９０−１２３４−５６７８",
        "postal_code": "100-0001",
        "date_birth": "19900102",
        "address": "東京都千代田区丸の内1-2-3",
        "email": "Taro@Example.CO.JP",
        "name_romaji": "Taro Takahashi",
        "party_type": "person",
    }
    normalized = normalize_record(raw)
    assert normalized["n_phone"] == "09012345678"
    assert normalized["n_postal"] == "1000001"
    assert normalized["n_dob"] == "1990-01-02"
    assert normalized["n_name_loose"] == "高橋太郎"
    # _compact strips punctuation (legacy email contract)
    assert normalized["n_email"] == "taro@examplecojp"
    assert "1000001" in normalized["retrieval_text"]
    from api.domain.normalize import normalize_frame

    frame = normalize_frame(pd.DataFrame([{"record_id": "R1", **raw}]), "record_id").iloc[0]
    for field, value in normalized.items():
        assert str(frame[field]) == value
    for norm, sample in (
        (normalize_phone, "０９０−１２３４−５６７８"),
        (normalize_postal, "100-0001"),
        (normalize_email, "Taro@Example.CO.JP"),
        (normalize_kana, "タカハシ タロウ"),
    ):
        once = norm(sample)
        assert norm(once) == once, f"{norm.__name__} must be idempotent"


def test_pair_feature_agreement_missing_and_conflict():
    from api.domain.features import PAIR_FEATURE_COLS, compute_pair_features
    from api.domain.normalize import normalize_record

    query = normalize_record(
        {
            "name_kanji": "佐藤 太郎",
            "phone": "090-1234-5678",
            "date_birth": "1980-05-05",
            "postal_code": "100-0001",
        }
    )
    candidates = pd.DataFrame(
        [
            normalize_record(
                {
                    "name_kanji": "佐藤 太郎",
                    "phone": "090-1234-5678",
                    "date_birth": "1980-05-05",
                    "postal_code": "100-0001",
                }
            ),
            normalize_record(
                {
                    "name_kanji": "鈴木 一郎",
                    "phone": "090-9999-9999",
                    "date_birth": "1970-01-01",
                    "postal_code": "150-0001",
                }
            ),
        ]
    )
    retrieval = pd.DataFrame(
        {"sparse_score": [0.8, 0.2], "dense_score": [0.0, 0.0], "exact_count": [3, 0]}
    )
    features = compute_pair_features(query, candidates, retrieval)
    assert list(features.columns) == PAIR_FEATURE_COLS
    assert features["name_loose_exact"][0] == 1 and features["phone_exact"][0] == 1
    assert features["agreement_count"][0] == 4  # name + dob + postal + phone
    assert features["missing_fraction"][0] == pytest.approx(0.5)  # email/kana/romaji/address unset
    assert features["agreement_count"][1] == 0
    assert features["dob_conflict"][1] == 1 and features["postal_conflict"][1] == 1
    assert features["retrieval_score"][0] == pytest.approx(0.8 + 0.08 * 3)


def test_entity_search_ranks_by_embedding_similarity():
    from api.infrastructure.retrieval.entity_search import EntitySearch

    class FakeIndex:
        reference_embeddings = np.array([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]], dtype=np.float32)

    class FakeEngine:
        _index = FakeIndex()
        _reference = pd.DataFrame({"record_id": ["R1", "R2", "R3"]})
        _record_to_nayose = {"R1": "N1", "R2": "N1", "R3": "N2"}
        _canonical = pd.DataFrame(
            [
                {
                    "nayose_id": "N1",
                    "name_kanji": "佐藤 太郎",
                    "name_kana": "サトウ タロウ",
                    "phone": "090-1",
                    "postal_code": "1000001",
                    "record_count": 2,
                },
                {
                    "nayose_id": "N2",
                    "name_kanji": "鈴木 一郎",
                    "name_kana": "スズキ イチロウ",
                    "phone": "080-2",
                    "postal_code": "1500001",
                    "record_count": 1,
                },
            ]
        )

    class FakeRetriever:
        def encode(self, text):
            return np.array([[1.0, 0.0]]) if "佐藤" in text else np.array([[0.0, 1.0]])

    search = EntitySearch(SimpleNamespace(engine=FakeEngine(), retriever=FakeRetriever()))
    hits = search.search("佐藤", top_k=5)
    assert hits is not None and hits[0]["nayose_id"] == "N1"
    assert hits[0]["score"] > 0.9
    assert hits[0]["name_kana"] == "サトウ タロウ"
    other = search.search("微妙な入力", top_k=5)
    assert other is not None and other[0]["nayose_id"] == "N2"
    assert search.search("") == []


def test_entity_auto_link_rejects_name_only_identity_evidence():
    from api.application.matching import ExplainedMatcher

    engine = fake_engine()
    engine.config.entity_margin_threshold = 0.0
    engine.config.minimum_evidence_families = 1
    result = ExplainedMatcher(engine).recommend({"name_kanji": "佐藤 太郎"}, lambda _: None)
    assert result["action"] == "REVIEW"
    assert result["reason"] == "evidence_safety_check"
    assert result["diagnostics"]["auto_eligibility"]["identity_anchor"] is False


def test_entity_auto_link_requires_margin_and_evidence_safety():
    from api.application.matching import ExplainedMatcher

    engine = fake_engine()
    engine.config.entity_margin_threshold = 0.06
    engine.config.minimum_evidence_families = 2
    result = ExplainedMatcher(engine).recommend(
        {
            "name_kanji": "佐藤 太郎",
            "phone": "09012345678",
            "date_birth": "2000-01-01",
        },
        lambda _: None,
    )
    assert result["action"] == "REVIEW"
    assert result["reason"] == "evidence_safety_check"


def test_threshold_overrides_move_only_the_decision_layer():
    from api.application.matching import ExplainedMatcher

    matcher = ExplainedMatcher(fake_engine())
    base = matcher.recommend(
        {
            "name_kanji": "佐藤 太郎",
            "phone": "09012345678",
            "date_birth": "2000-01-01",
        },
        lambda _: None,
    )
    assert base["threshold_source"] == "manifest"
    # lower auto threshold -> the same score becomes AUTO_LINK
    tuned = matcher.recommend(
        {
            "name_kanji": "佐藤 太郎",
            "phone": "09012345678",
            "date_birth": "2000-01-01",
        },
        lambda _: None,
        overrides={"auto": 0.7, "review": 0.2},
    )
    assert tuned["action"] == "AUTO_LINK"
    assert tuned["threshold_source"] == "session"
    assert tuned["top_probability"] == base["top_probability"], "scores never change"
    # invalid override (auto < review) silently falls back to the manifest
    invalid = matcher.recommend(
        {
            "name_kanji": "佐藤 太郎",
            "phone": "09012345678",
            "date_birth": "2000-01-01",
        },
        lambda _: None,
        overrides={"auto": 0.1, "review": 0.5},
    )
    assert invalid["threshold_source"] == "manifest"
    assert invalid["thresholds"] == base["thresholds"]


def test_input_normalized_and_no_match_nearest_are_disclosed():
    from api.application.matching import ExplainedMatcher

    matcher = ExplainedMatcher(fake_engine())
    result = matcher.recommend(
        {"name_kanji": "佐藤 太郎", "phone": "090-1234-5678"}, lambda _: None
    )
    assert result["input_normalized"]["name_kanji"] == "佐藤太郎"
    assert result["input_normalized"]["phone"] == "09012345678"
    strict = ExplainedMatcher(fake_engine())
    strict.engine.config = SimpleNamespace(
        entity_auto_threshold=0.99, entity_review_threshold=0.985
    )
    denied = strict.recommend({"name_kanji": "佐藤 太郎", "phone": "09012345678"}, lambda _: None)
    assert denied["action"] == "NO_MATCH"
    assert denied["nearest"] and denied["nearest"][0]["nayose_id"] == "N1"


def test_score_context_reports_decision_load(tmp_path):
    from api.application.matching import RecommendationService

    path = tmp_path / "review_queue.parquet"
    pd.DataFrame({"pair_probability": [0.9, 0.7, 0.5]}).to_parquet(path, index=False)
    ctx = RecommendationService._score_context(tmp_path, 0.7)
    assert ctx["at_or_above"] == 2 and ctx["total"] == 3
    assert ctx["pct"] == 66.7


def test_engine_session_closes_previous_engine_before_swap(tmp_path):
    from api.application.matching_runtime import EngineSession

    run_paths = {name: tmp_path / name for name in ("run-a", "run-b")}
    events: list[str] = []
    handles: list[object] = []

    class Handle:
        def __init__(self, run_id: str):
            self.run_id = run_id

        def recommend(self, record, stage, overrides=None):
            return {"top_probability": 0, "candidates": []}

        def close(self):
            events.append(f"close:{self.run_id}")

    def loader(run_dir, _data_dir):
        run_id = run_dir.name
        events.append(f"load:{run_id}")
        handle = Handle(run_id)
        handles.append(handle)
        return handle

    repo = SimpleNamespace(resolve=lambda run_id: run_paths[run_id])
    session = EngineSession(repo, tmp_path / "data", loader)
    try:
        session.recommend("run-a", {"name_kanji": "佐藤"})
        session.recommend("run-b", {"name_kanji": "鈴木"})
    finally:
        session.close()

    assert events[:3] == ["load:run-a", "close:run-a", "load:run-b"]
    assert events[-1] == "close:run-b"
    assert len(handles) == 2


def test_matching_job_status_survives_service_restart(tmp_path):
    from api.application.matching import RecommendationService

    repo = SimpleNamespace(resolve=lambda _run_id: tmp_path / "run")
    service = RecommendationService(repo, tmp_path / "data", loader=lambda *_: None)
    try:
        job = service.start("run", {})
        service.wait(job["id"], timeout=5)
    finally:
        service.close()

    restarted = RecommendationService(repo, tmp_path / "data", loader=lambda *_: None)
    try:
        status = restarted.status(job["id"])
    finally:
        restarted.close()

    assert status["stage"] == "complete"
    assert status["result"]["action"] == "INSUFFICIENT_INPUT"


def test_matching_job_cancellation_is_persisted(tmp_path):
    from api.application.matching import ExplainedMatcher, RecommendationService

    started, release = Event(), Event()

    def load(run, data):
        started.set()
        assert release.wait(5)
        return ExplainedMatcher(fake_engine())

    repo = SimpleNamespace(resolve=lambda _: tmp_path)
    service = RecommendationService(repo, tmp_path, loader=load)
    try:
        job = service.start("run", {"name_kanji": "佐藤"})
        assert started.wait(5)
        status = service.cancel(job["id"])
        assert status["stage"] == "cancelled"
        release.set()
        service.wait(job["id"], timeout=5)
        assert service.status(job["id"])["stage"] == "cancelled"
        assert service.status(job["id"])["error"] == "request_cancelled"
    finally:
        release.set()
        service.close()


def test_async_jobs_report_real_loading_and_reuse_engine(tmp_path):
    from api.application.matching import ExplainedMatcher, RecommendationService

    started, release = Event(), Event()
    loads = []

    def load(run, data):
        loads.append(run)
        started.set()
        assert release.wait(5)
        return ExplainedMatcher(fake_engine())

    repo = SimpleNamespace(resolve=lambda _: tmp_path)
    service = RecommendationService(repo, tmp_path, loader=load)
    try:
        job = service.start("run", {"name_kanji": "佐藤"})
        assert started.wait(5)
        assert service.status(job["id"])["stage"] == "loading"
        empty = service.start("run", {})
        assert empty["stage"] == "complete"
        assert empty["result"]["action"] == "INSUFFICIENT_INPUT"
        release.set()
        service.wait(job["id"], timeout=5)
        done = service.status(job["id"])
        assert done["stage"] == "complete"
        assert done["result"]["action"] == "REVIEW"
        loading = [s for s in done["timeline"] if s["stage"] == "loading"]
        assert len(loading) == 1, "loading stamped once with its real duration"
        assert loading[0]["elapsed_ms"] >= 0
        service.recommend("run", {"name_kanji": "佐藤"})
        assert len(loads) == 1
        assert all(item["elapsed_ms"] >= 0 for item in done["timeline"])
    finally:
        release.set()
        service.close()
