# Search architecture contract

Search is a server-owned pipeline. The browser submits a query and filters through `QueryPort`; it never normalizes, ranks, merges, or queries data directly.

## Required composition

```text
SearchRequest
  -> QueryInterpreter
  -> CandidateRetriever strategies
  -> CandidateMerger
  -> CandidateRanker strategies
  -> SearchResponseBuilder
  -> SearchResponse + evidence + telemetry
```

The composition root is `api/main.py` (`build_search_service` Factory Method). The pipeline implementation is `api/search_service.py`.

| Component | Pattern | Responsibility |
|---|---|---|
| `build_search_service` | Factory Method | Composes the server-owned search graph |
| `SearchService` | Facade | Coordinates one search request |
| `QueryInterpreter` | Strategy registry | Builds the immutable `QueryPlan` |
| `QueryIntentRule` | Strategy | Adds one field-intent signal |
| `ExactRetriever`, `FuzzyRetriever`, `SemanticRetriever` | Strategy + Adapter | Adapts retrieval backends to one protocol |
| `CandidateMerger` | Composite | Deduplicates entity candidates and preserves evidence |
| `IntentAwareRanker` | Strategy registry | Selects a ranking strategy by retrieval source |
| `SearchResponseBuilder` | Builder | Preserves the API response contract |
| `SuggestionRetriever` | Adapter | Exposes legacy correction suggestions through a port |
| `MemoProxy` | Decorator/Proxy | Caches transport reads, including filters |
| `SearchWorkspace` | State + Observer | Manages query, filters, pending requests, and selection |

## SOLID rules

- **SRP:** normalization, intent detection, retrieval, merging, ranking, response construction, and transport are separate responsibilities.
- **OCP:** add an intent rule, retriever, or rank strategy by registering a new Strategy; do not add a source conditional to `SearchService`.
- **LSP:** every retriever returns the same candidate shape and may be substituted without changing orchestration.
- **ISP:** backend protocols contain only the operation needed by their adapter.
- **DIP:** `SearchService` depends on retriever, planner, merger, ranker, builder, and suggestion protocols; FastAPI owns composition.

## Non-negotiable boundaries

- No SQL, `fetch`, `localStorage`, normalization, or ranking in `frontend/ts` views.
- Search filters are part of the server request and are applied to every retrieval source.
- Every merged result retains `retrieval_sources` and `retrieval_evidence`.
- Every response includes `query_plan` and per-stage `search_trace` telemetry.
- Exact results remain ahead of semantic/fuzzy results unless a future policy explicitly changes that contract.
- New retrieval behavior must be a Strategy; do not extend `SearchService` with backend-specific branches.

`tests/test_search_arch_contract.py` is the executable enforcement layer. `tests/test_arch_contract.py` enforces the broader frontend and CSS architecture. Run `python scripts/audit_repo.py` before merging.
