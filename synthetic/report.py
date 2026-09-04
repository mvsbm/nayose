"""Audit dashboard renderer: run bundle -> self-contained ``dashboard.html``.

Google-style task-first design: one core job (find who this record is and why)
with progressive disclosure (KPIs -> confusion/review -> entity card with
field-lineage summary -> fuzzy search). Contract-first: the bundle schema is
validated at render time and failures are explicit.

Deliberate, documented exceptions to the front-end checklist:
- single-file artifact (shareable, offline) => CSS/JS are inline by design; a
  `<meta http-equiv="Content-Security-Policy">` still documents the intent.
- web-deployment-only rules (HTTP caching, CDN/SRI, SEO crawlers, PWA) are
  not applicable to a local file (mapped in README).
"""

from __future__ import annotations

import html
import json
from pathlib import Path

import pandas as pd

REQUIRED_COLUMNS = {
    "full_dataset_summary": ["metric", "value"],
    "full_dataset_metrics": ["section", "metric", "value"],
    "pair_confusion_test": ["metric", "value"],
    "query_confusion": ["scenario", "action", "expected_no", "expected_yes"],
    "review_queue": ["left_record_id", "right_record_id"],
    "canonical_entities": ["nayose_id", "name_kanji", "name_kana", "record_count"],
    "record_to_nayose": ["record_id", "nayose_id"],
}


def _load_frames(run: Path, data_dir: Path) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    for stem, required in REQUIRED_COLUMNS.items():
        path = run / f"{stem}.parquet"
        if not path.exists():
            raise ValueError(f"dashboard: missing bundle frame {path}")
        frame = pd.read_parquet(path)
        missing = [c for c in required if c not in frame.columns]
        if missing:
            raise ValueError(f"dashboard: {stem} missing required columns {missing}")
        frames[stem] = frame
    ref = data_dir / "reference_records.parquet"
    lineage = data_dir / "ground_truth" / "field_lineage.parquet"
    if not ref.exists() or not lineage.exists():
        raise ValueError("dashboard: reference_records / field_lineage missing in data_dir")
    frames["reference_records"] = pd.read_parquet(ref)
    frames["field_lineage"] = pd.read_parquet(lineage)
    return frames


def _kpis(frames: dict[str, pd.DataFrame]) -> dict[str, float]:
    metrics = frames["full_dataset_metrics"].set_index(["section", "metric"]).value
    summary = frames["full_dataset_summary"].set_index("metric").value
    out: dict[str, float] = {}
    out["candidate_recall"] = float(summary.get("candidate_pair_recall", float("nan")))
    if ("candidate", "pair_recall") in metrics.index:
        out["candidate_recall"] = float(metrics.loc[("candidate", "pair_recall")])
    p = float(summary.get("b3_precision", float("nan")))
    r = float(summary.get("b3_recall", float("nan")))
    out["b3_f1"] = 2 * p * r / (p + r) if p == p and r == r and p + r > 0 else float("nan")
    for label, section, metric in (
        ("top1", "incoming_query", "top1_identity_accuracy_existing"),
        ("mrr", "incoming_query", "mean_reciprocal_rank_existing"),
        ("review", "incoming_query", "review_rate"),
        ("no_match", "incoming_query", "no_match_rate"),
    ):
        if (section, metric) in metrics.index:
            out[label] = float(metrics.loc[(section, metric)])
    return out


def _confusion_html(frames: dict[str, pd.DataFrame]) -> str:
    pc = frames["pair_confusion_test"].set_index("metric").value
    labels = (
        "true_positives",
        "false_positives",
        "true_negatives",
        "false_negatives",
        "precision",
        "recall",
        "specificity",
        "f1",
        "auc",
    )
    cells = "".join(
        f'<tr><th scope="row">{html.escape(m)}</th><td>{pc.get(m, float("nan")):.4f}</td></tr>'
        for m in labels
        if m in pc.index
    )
    pairs = f"<table><caption>Pair verifier — unseen test</caption><tbody>{cells}</tbody></table>"
    qc = frames["query_confusion"]
    pivot = qc.pivot_table(
        index="scenario", columns="action", values="expected_yes", aggfunc="sum"
    ).fillna(0)
    actions = [
        c for c in ("AUTO_LINK", "REVIEW", "NO_MATCH", "INSUFFICIENT_INPUT") if c in pivot.columns
    ]
    head = "<tr><th>scenario</th>" + "".join(f"<th>{a}</th>" for a in actions) + "</tr>"
    body = "".join(
        "<tr><th scope='row'>"
        + html.escape(str(s))
        + "</th>"
        + "".join(f"<td>{int(pivot.loc[s, a])}</td>" for a in actions)
        + "</tr>"
        for s in pivot.index
    )
    return f"{pairs}<table><caption>Query decisions — expected-existing counts</caption><thead>{head}</thead><tbody>{body}</tbody></table>"


def _review_rows(frames: dict[str, pd.DataFrame], limit: int = 40) -> list[dict]:
    rq = frames["review_queue"]
    mapping = frames["record_to_nayose"].set_index("record_id").nayose_id.to_dict()
    rows = []
    for _, row in rq.head(limit).iterrows():
        rows.append(
            {
                "l": str(row.get("left_record_id", "")),
                "r": str(row.get("right_record_id", "")),
                "lp": float(row.get("pair_probability", row.get("retrieval_score", 0.0))),
                "ln": mapping.get(str(row.get("left_record_id", "")), ""),
                "rn": mapping.get(str(row.get("right_record_id", "")), ""),
            }
        )
    return rows


def _entity_index(frames: dict[str, pd.DataFrame]) -> list[dict]:
    """Compact entity index + per-entity lineage summary + member records."""
    canon = frames["canonical_entities"]
    entities: list[dict] = []
    fields = [
        c
        for c in (
            "name_kanji",
            "name_kana",
            "name_romaji",
            "date_birth",
            "postal_code",
            "address",
            "phone",
            "email",
        )
        if c in canon.columns
    ]
    rec_df = frames["reference_records"]
    rec_keep = [
        c
        for c in ("record_id", "name_kanji", "name_kana", "phone", "postal_code")
        if c in rec_df.columns
    ]
    rec_by_ent: dict[str, list[dict]] = {}
    if rec_keep:
        r2n = frames["record_to_nayose"].set_index("record_id").nayose_id
        subset = rec_df[rec_keep].copy()
        subset["nayose_id"] = subset.record_id.map(r2n)
        for nid, g in subset.groupby("nayose_id"):
            rec_by_ent[str(nid)] = g.drop(columns=["nayose_id"]).to_dict("records")
    flo = frames["field_lineage"]
    fl_cols = [c for c in ("item_id", "field", "transformation") if c in flo.columns]
    lineage_by_ent: dict[str, dict[str, list[str]]] = {}
    if len(fl_cols) == 3:
        r2n = frames["record_to_nayose"].set_index("record_id").nayose_id
        fl = flo[fl_cols].copy()
        fl["nayose_id"] = fl.item_id.map(r2n).astype(str)
        groups = fl.groupby(["nayose_id", "field"])["transformation"].agg(
            lambda s: list(dict.fromkeys(s))
        )
        for (nid, field), labels in groups.items():
            lineage_by_ent.setdefault(nid, {})[field] = labels if len(labels) <= 4 else labels[:4]
    for _, row in canon.iterrows():
        nid = str(row["nayose_id"])
        entities.append(
            {
                "id": nid,
                "name": str(row.get("name_kanji", "")),
                "kana": str(row.get("name_kana", "")),
                "records": int(row.get("record_count", 0)),
                "fields": {f: str(row.get(f, "")) for f in fields},
                "members": rec_by_ent.get(nid, [])[:6],
                "lineage": lineage_by_ent.get(nid, {}),
            }
        )
    return entities


def css() -> str:
    return """
:root{
  --bg:#f8fafc; --fg:#0f172a; --muted:#64748b; --line:#e2e8f0;
  --accent:#4f46e5; --ok:#16a34a; --warn:#d97706; --bad:#dc2626; --blank:#94a3b8;
  --pad:clamp(.75rem,2vw,1.5rem); --radius:.5rem;
}
[data-theme="dark"]{--bg:#0b1220;--fg:#e2e8f0;--muted:#94a3b8;--line:#1e293b;--accent:#818cf8}
*{box-sizing:border-box}
html{color-scheme:light dark}
body{margin:0;background:var(--bg);color:var(--fg);
  font-family:"Noto Sans JP",ui-sans-serif,system-ui,"Hiragino Kaku Gothic ProN","Yu Gothic",sans-serif;
  line-height:1.55}
header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);padding:.6rem var(--pad);display:flex;gap:1rem;align-items:center;flex-wrap:wrap}
h1{font-size:1.05rem;margin:0;font-weight:600}
nav[role="tablist"]{display:flex;gap:.25rem;flex-wrap:wrap}
button.tab{background:none;border:1px solid transparent;border-radius:999px;padding:.35rem .8rem;cursor:pointer;color:var(--muted);font:inherit;font-size:.85rem}
button.tab[aria-selected="true"]{background:var(--accent);color:#fff}
main{padding:var(--pad);display:grid;gap:var(--pad);grid-template-columns:1fr}
.panel{display:none}.panel.active{display:grid;gap:var(--pad)}
.kpi{display:flex;gap:.8rem;flex-wrap:wrap}
.kpi .card{flex:1 1 12rem;background:#fff;border:1px solid var(--line);border-radius:var(--radius);padding:.9rem 1rem}
[data-theme="dark"] .kpi .card{background:#111c33}
.kpi .v{font-size:1.6rem;font-weight:700;font-variant-numeric:tabular-nums}
.kpi .l{font-size:.78rem;color:var(--muted)}
.chip{display:inline-block;border-radius:999px;padding:.1rem .6rem;font-size:.78rem;font-weight:600}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}.blank{color:var(--blank)}
table{width:100%;border-collapse:collapse;font-size:.86rem;background:#fff}
[data-theme="dark"] table{background:#111c33}
th,td{border-bottom:1px solid var(--line);padding:.45rem .6rem;text-align:left;vertical-align:top}
th{font-weight:600;color:var(--muted);position:sticky;top:3.2rem;background:var(--bg)}
caption{caption-side:top;text-align:left;font-weight:600;padding:.5rem 0}
input[type="search"]{width:min(28rem,100%);padding:.5rem .8rem;border:1px solid var(--line);border-radius:var(--radius);font:inherit;background:#fff;color:var(--fg)}
input[type="search"]:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.card{border:1px solid var(--line);border-radius:var(--radius);padding:.8rem 1rem;background:#fff}
[data-theme="dark"] .card{background:#111c33}
.card h3{margin:.1rem 0 .4rem;font-size:.95rem}
.members{font-size:.8rem;color:var(--muted)}
.lin{display:flex;gap:.5rem;flex-wrap:wrap;margin-top:.4rem}
.lin span{font-size:.72rem;border:1px solid var(--line);border-radius:999px;padding:.05rem .5rem}
button.more{background:none;border:1px solid var(--line);border-radius:var(--radius);padding:.4rem .9rem;cursor:pointer;font:inherit;color:var(--fg)}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
fieldset{border:0;padding:0;margin:0}
"""


def js() -> str:
    return """
(() => {
  const DATA = JSON.parse(document.getElementById('app-data').textContent);
  const $ = (s, el=document) => el.querySelector(s);
  const $$ = (s, el=document) => [...el.querySelectorAll(s)];
  // tabs (keyboard accessible)
  const tabs = $$('.tab');
  const activate = id => {
    tabs.forEach(t => t.setAttribute('aria-selected', String(t.dataset.tab === id)));
    $$('.panel').forEach(p => p.classList.toggle('active', p.id === 'panel-' + id));
    $('#tab-' + id) && $('#tab-' + id).focus();
  };
  tabs.forEach(t => t.addEventListener('click', () => activate(t.dataset.tab)));
  document.addEventListener('keydown', e => {
    if (!e.key.startsWith('Arrow')) return;
    const i = tabs.findIndex(t => t.getAttribute('aria-selected') === 'true');
    const n = (i + (e.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length;
    activate(tabs[n].dataset.tab);
  });
  // theme
  $('#theme').addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
  });
  // KPIs
  const k = DATA.kpis;
  const kpi = (v, l) => `<div class="card"><div class="v">${v}</div><div class="l">${l}</div></div>`;
  $('#kpis').innerHTML =
    kpi((k.candidate_recall*100).toFixed(2)+'%','candidate recall') +
    kpi((k.b3_f1*100).toFixed(2)+'%','B3 F1') +
    kpi((k.top1*100).toFixed(2)+'%','top-1 identity') +
    kpi((k.mrr*100).toFixed(2)+'%','MRR') +
    kpi((k.review*100).toFixed(2)+'%','review') +
    kpi((k.no_match*100).toFixed(2)+'%','no-match');
  // search (debounced, capped)
  const idx = DATA.entities;
  const box = $('#search'), list = $('#search-results');
  let timer;
  box.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      const q = box.value.trim().toLowerCase();
      list.innerHTML = '';
      if (q.length < 2) return;
      const hits = [];
      for (const e of idx) {
        const hay = (e.name + ' ' + e.kana + ' ' + (e.fields.phone||'') + ' ' + (e.fields.postal_code||'')).toLowerCase();
        if (hay.includes(q)) { hits.push(e); if (hits.length >= 50) break; }
      }
      list.innerHTML = hits.map(e =>
        `<div class="card"><h3><a href="#entity-${e.id}">${esc(e.name)}</a> ${esc(e.kana||'')}</h3>` +
        `<div class="members">${e.records} records · ${(e.fields.phone||'')} · ${e.fields.postal_code||''}</div></div>`
      ).join('') || '<p class="blank">該当なし</p>';
    }, 150);
  });
  // entity cards (paged)
  const page = $('#review');
  $$('#entity-tab button.more').forEach(b => b.addEventListener('click', () => {
    const n = parseInt(b.dataset.step,10), cur = parseInt(b.dataset.n,10);
    showEntities(cur + n, b.dataset.step);
  }));
  function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
  function showEntities(end, step){
    step = parseInt(step||'50',10);
    const box = $('#entity-list');
    const slice = idx.slice(0, end);
    box.innerHTML = slice.map(cardHtml).join('');
    const more = $('#entity-more');
    more.hidden = end >= idx.length;
    more.dataset.n = end; more.dataset.step = step;
  }
  function cardHtml(e){
    const ln = Object.entries(e.lineage||{}).map(([f, ls]) =>
      `<div class="lin"><span>${esc(f)}</span>${ls.map(x=>`<span>${esc(x)}</span>`).join('')}</div>`).join('');
    const members = (e.members||[]).map(m => `<div class="members">${esc(m.record_id)} · ${esc(m.phone||'')}</div>`).join('');
    return `<div class="card" id="entity-${esc(e.id)}"><h3>${esc(e.name)} <span class="blank">${esc(e.kana||'')}</span></h3>` +
      `<div class="members">${e.records} records · ${esc(e.fields.postal_code||'')} · ${esc(e.fields.address||'')}</div>${members}${ln}</div>`;
  }
  showEntities(50, 50);
  if (location.hash) activate('entities');
})();
"""


def render(run: Path | str, data_dir: Path | str, out: Path | None = None) -> Path:
    run, data_dir = Path(run), Path(data_dir)
    frames = _load_frames(run, data_dir)
    kpis = _kpis(frames)
    review_rows = _review_rows(frames)
    app_data = {
        "kpis": {k: (v if v == v else 0.0) for k, v in kpis.items()},
        "entities": _entity_index(frames),
        "review": review_rows,
    }
    head = (
        '<!doctype html>\n<html lang="ja" data-theme="light">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="description" content="Nayose 照合監査ダッシュボード">\n'
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'self' 'unsafe-inline'\">\n"
        f"<title>Nayose 照合監査 — {run.name}</title>\n<style>{css()}</style>\n</head>\n<body>"
    )
    header = (
        "<header><h1>Nayose 照合監査</h1>"
        '<nav role="tablist" aria-label="セクション">'
        + "".join(
            f'<button class="tab" data-tab="{tid}" id="tab-{tid}" role="tab" '
            f'aria-selected="{"true" if i == 0 else "false"}" aria-controls="panel-{tid}">{label}</button>'
            for i, (tid, label) in enumerate(
                [
                    ("kpi", "指標"),
                    ("confusion", "混同行列"),
                    ("review", "レビュー待機"),
                    ("entities", "エンティティ"),
                    ("search", "検索"),
                ]
            )
        )
        + '<button id="theme" type="button" aria-label="テーマ切替">テーマ</button></nav></header>'
    )
    review_body = "".join(
        "<tr><td>"
        + html.escape(r["l"])
        + "</td><td>"
        + html.escape(r["r"])
        + f"</td><td>{r['lp']:.3f}</td></tr>"
        for r in review_rows
    )
    panels = (
        '<main><section id="panel-kpi" class="panel active" aria-label="指標"><div id="kpis" class="kpi"></div></section>'
        '<section id="panel-confusion" class="panel" aria-label="混同行列">'
        + _confusion_html(frames)
        + "</section>"
        '<section id="panel-review" class="panel" aria-label="レビュー待機">'
        "<table><caption>レビュー待機エッジ（先頭のみ）</caption>"
        "<thead><tr><th>左レコード</th><th>右レコード</th><th>確率</th></tr></thead>"
        + "<tbody>"
        + review_body
        + "</tbody></table></section>"
        '<section id="panel-entities" class="panel" aria-label="エンティティ">'
        '<div id="entity-list" role="list"></div>'
        '<button id="entity-more" type="button" class="more">もっと読む</button></section>'
        '<section id="panel-search" class="panel" aria-label="検索">'
        '<form role="search" onsubmit="return false">'
        '<label for="search">氏名・電話・郵便番号で検索</label><br>'
        '<input type="search" id="search" name="search" placeholder="例：佐藤 太郎" autocomplete="off" aria-describedby="search-results">'
        '</form><div id="search-results" role="status" aria-live="polite"></div></section></main>'
    )
    data_script = (
        '<script id="app-data" type="application/json">'
        + json.dumps(app_data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
        + "</script>"
    )
    page = head + header + panels + data_script + f"<script>{js()}</script>" + "</body>\n</html>\n"
    if out is None:
        out = run / "dashboard.html"
    out.write_text(page, encoding="utf-8")
    for marker in (
        "<!doctype html",
        'role="search"',
        "data-theme",
        'id="app-data"',
        'aria-live="polite"',
    ):
        assert marker in page, f"dashboard render missing {marker}"
    return out
