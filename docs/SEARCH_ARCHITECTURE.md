# Search architecture contract

Search is a server-owned pipeline. The browser submits a query and filters through the search port; it never normalizes, ranks, merges, or queries data directly.

## Required composition

```text
SearchRequest
  -> QueryInterpreter
  -> CandidateRetriever strategies
  -> CandidateMerger
  -> CandidateRanker strategies
  -> SearchResponseBuilder
  -> SearchResponse + evidence + telemetry

EntitySearchRequest
  -> QueryInterpreter
  -> ExactPageRetriever
  -> exhaustive exact page
  -> bounded hybrid fallback when relevance search has no exact hits
```

The composition root is `src/api/composition.py` (`build_search_service`). The pipeline implementation is `src/api/application/search.py`.

Dense candidate discovery uses immutable, fingerprint-bound artifacts from
`src/api/infrastructure/retrieval/dense_views.py`: record embeddings remain the broad view, while separate
name, address, and alias indexes preserve field scope. `NameVariantExpander`
creates search-only aliases with the source record, generation method, and
confidence; aliases never rewrite canonical entity profiles. Record candidates retain exact-block, sparse, record-dense, and field-view
scores and ranks, then use conservative, query-aware weighted reciprocal-rank fusion before
pair verification. The full record-dense rank is primary; only views relevant
to populated query fields contribute, and name, address, and alias views remain
lower-weight correlated discovery signals. Field-view scores never overwrite
the record-dense feature consumed by the trained verifier.
Dense evidence is discovery evidence, not an identity decision. Offline candidate graph construction uses the bounded sparse backend in `src/api/training/retrieval.py`; its capped CSC postings path shares the serving query-feature cap instead of materializing all-pairs sparse scores. Exact block indexes are fingerprint-bound and stored with deflate compression.

| Component | Responsibility |
|---|---|
| `build_search_service` | Composes the server-owned search graph |
| `SearchService` | Coordinates one search request |
| `QueryInterpreter` | Builds the immutable `QueryPlan` |
| `QueryIntentRule` | Adds one field-intent signal |
| `ExactRetriever`, `FuzzyRetriever`, `SemanticRetriever` | Adapt retrieval backends to one candidate shape |
| `ExactPageRetriever` | Adapts the count-aware exact repository for Explorer pagination |
| `EntitySearchRequest` | Carries shared query, filter, sort, and page state |
| `CandidateMerger` | Deduplicates entity candidates and preserves evidence |
| `IntentAwareRanker` | Selects ranking behavior by retrieval source |
| `SearchResponseBuilder` | Preserves the API response contract |
| `SuggestionRetriever` | Exposes correction suggestions through the search port |
| `MemoProxy` | Caches transport reads, including filters |
| `SearchWorkspace` | Manages query, filters, pending requests, and selection |

## Extension rules

- Keep normalization, intent detection, retrieval, merging, ranking, response construction, and transport in separate modules.
- Add an intent rule, retriever, or ranking implementation through the existing registrations; do not add a source-specific conditional to `SearchService`.
- Retrievers return the same candidate shape so orchestration does not depend on a concrete backend.
- Keep each backend port limited to the operation its adapter needs.
- Keep composition in FastAPI and inject search collaborators into `SearchService`.

## Non-negotiable boundaries

- No SQL, `fetch`, `localStorage`, normalization, or ranking in `frontend/ts` views.
- Search filters are part of the server request and are applied to every retrieval source.
- Every merged result retains `retrieval_sources` and `retrieval_evidence`.
- Every response includes `query_plan` and per-stage `search_trace` telemetry.
- Exact results remain ahead of semantic/fuzzy results unless a future policy explicitly changes that contract.
- Explorer reports `total_is_exact=false` for bounded similarity fallback results; it never presents a candidate window as an exhaustive corpus count.
- New retrieval behavior must register a retriever; do not extend `SearchService` with backend-specific branches.
- Build dense views in batches and cache them inside the generation by data fingerprint and model ID; never materialize one destructive entity centroid.
- Keep corpus and query text shapes identical. Alias retrieval searches each observed name variant as a separate `name=<value>` query rather than embedding a multi-alias query against single-alias rows.
- Keep the labeled retrieval-text schema shared by offline training and API serving; a bundle must not score an unstructured query against a labeled corpus.
- Treat auto-linking as a safety decision: require calibrated probability, margin, independent evidence families, no hard field conflicts, and an exact date-of-birth or email anchor; phone-only evidence remains reviewable because household contacts can be shared.

`tests/test_search_architecture_contract.py` is the executable enforcement layer. `tests/test_architecture_contract.py` enforces the broader frontend and CSS architecture. Run `python -m api.cli audit repo` before merging.
