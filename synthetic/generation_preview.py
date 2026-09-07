"""Server-owned generation controls, catalog metadata, and dry-run estimates."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import date
from typing import Any

from .config import (
    CHANNEL_NAMES,
    DEFAULT_STALE_RATE,
    DIFFICULTY,
    EVENT_NAMES,
    NOISE_FIELDS,
    PROFILES,
    QUERY_MIX,
    STALE_EVENT_NAMES,
    NoiseConfig,
    SyntheticConfig,
    noise_for_config,
)
from .generation_sample import build_examples, build_samples

PROFILE_INFO: dict[str, dict[str, str]] = {
    "preflight": {
        "label_ja": "事前確認",
        "label_en": "Preflight",
        "description_ja": "最小構成。設定確認と短い反復に向きます。",
        "description_en": "Smallest run for checking settings and quick iterations.",
    },
    "smoke": {
        "label_ja": "スモーク",
        "label_en": "Smoke",
        "description_ja": "UI とモデルの動作確認に適した小規模データです。",
        "description_en": "Small dataset for UI and model checks.",
    },
    "mac": {
        "label_ja": "Mac 標準",
        "label_en": "Mac standard",
        "description_ja": "ローカル Mac での実験向けです。",
        "description_en": "For local experiments on a Mac.",
    },
    "large_mac": {
        "label_ja": "Mac 大規模",
        "label_en": "Mac large",
        "description_ja": "より多くのレコードで評価します。時間と容量に注意してください。",
        "description_en": "Larger evaluation run; check time and storage first.",
    },
    "generate_only": {
        "label_ja": "生成専用",
        "label_en": "Generate only",
        "description_ja": "大規模データ生成向けです。プレビューを必ず確認してください。",
        "description_en": "Large generation profile; review the preview before running.",
    },
}

DIFFICULTY_INFO: dict[str, dict[str, str]] = {
    "debug": {
        "label_ja": "デバッグ",
        "label_en": "Debug",
        "description_ja": "汚れを抑え、原因を追いやすくします。",
        "description_en": "Low corruption for tracing behavior.",
    },
    "realistic": {
        "label_ja": "現実的",
        "label_en": "Realistic",
        "description_ja": "通常の業務データに近い混在状態です。",
        "description_en": "A mixed condition close to operational data.",
    },
    "severe": {
        "label_ja": "厳しい",
        "label_en": "Severe",
        "description_ja": "難しい照合ケースを多く含めます。",
        "description_en": "More difficult resolution cases.",
    },
}

EVENT_INFO: dict[str, dict[str, Any]] = {
    "rename_stale": {
        "group": "history",
        "label_ja": "旧姓・旧名",
        "label_en": "Stale name",
        "description_ja": "改名後も以前の名前が残る",
        "description_en": "An old name remains after a rename",
        "example_ja": "山田 太郎 → 旧名 山田 一郎",
        "example_en": "Yamada Taro → former name Yamada Ichiro",
        "fields": ["name_kanji", "name_kana", "name_romaji"],
    },
    "move_stale": {
        "group": "history",
        "label_ja": "旧住所",
        "label_en": "Stale address",
        "description_ja": "転居前の住所と郵便番号が残る",
        "description_en": "The former address and postal code remain",
        "example_ja": "〒100-0001 → 〒150-0001（旧住所）",
        "example_en": "〒100-0001 → 〒150-0001 (former address)",
        "fields": ["postal_code", "address"],
    },
    "contact_change": {
        "group": "history",
        "label_ja": "旧連絡先",
        "label_en": "Stale contact",
        "description_ja": "変更前の電話・メールが残る",
        "description_en": "The former phone and email remain",
        "example_ja": "090-1111-2222 → 090-3333-4444（旧番号）",
        "example_en": "090-1111-2222 → 090-3333-4444 (former number)",
        "fields": ["phone", "email"],
    },
    "full_snapshot": {
        "group": "history",
        "label_ja": "過去スナップショット",
        "label_en": "Full stale snapshot",
        "description_ja": "名前・住所・連絡先が同じ過去時点でそろう",
        "description_en": "Name, address, and contact fields come from one past state",
        "example_ja": "旧名・旧住所・旧電話が一緒に記録される",
        "example_en": "Former name, address, and phone appear together",
        "fields": ["name_kanji", "address", "phone", "email"],
    },
    "contact_omission": {
        "group": "missing",
        "label_ja": "連絡先ブロック欠落",
        "label_en": "Contact omission",
        "description_ja": "電話とメールが一緒に空欄になる",
        "description_en": "Phone and email disappear together",
        "example_ja": "電話・メール: 空欄",
        "example_en": "Phone and email: blank",
        "fields": ["phone", "email"],
    },
    "paper_ocr": {
        "group": "document",
        "label_ja": "紙 OCR 誤読",
        "label_en": "Paper OCR",
        "description_ja": "紙書類の漢字・住所を OCR が取り違える",
        "description_en": "OCR confuses kanji and address text",
        "example_ja": "未 → 末、住所の一部が別字",
        "example_en": "未 → 末; one address character is misread",
        "fields": ["name_kanji", "name_kana", "address"],
    },
    "legacy_format": {
        "group": "format",
        "label_ja": "旧システム形式",
        "label_en": "Legacy format",
        "description_ja": "和暦・半角カナ・旧電話形式になる",
        "description_en": "Era dates, half-width kana, and old phone formats",
        "example_ja": "2020-04-01 → 令和2年4月1日",
        "example_en": "2020-04-01 → Reiwa 2-04-01",
        "fields": ["date_birth", "name_kana", "phone"],
    },
    "doc_damage": {
        "group": "missing",
        "label_ja": "書類ブロック欠損",
        "label_en": "Document damage",
        "description_ja": "名前・連絡先・所在地の一群が欠落する",
        "description_en": "A name, contact, or location block is missing",
        "example_ja": "名前3項目または住所2項目が空欄",
        "example_en": "A name, contact, or location block is blank",
        "fields": ["name_kanji", "phone", "address"],
    },
    "wrong_party_type": {
        "group": "identity",
        "label_ja": "種別取り違え",
        "label_en": "Wrong party type",
        "description_ja": "個人と法人の種別が反転する",
        "description_en": "Person and company type is flipped",
        "example_ja": "person → company",
        "example_en": "person → company",
        "fields": ["party_type"],
    },
    "alpha_in_kana": {
        "group": "name",
        "label_ja": "カナ内アルファベット",
        "label_en": "Latin in kana",
        "description_ja": "読みの一部が英字になる",
        "description_en": "Part of the reading becomes a Latin letter",
        "example_ja": "サトウ → サAトウ",
        "example_en": "サトウ → サAトウ",
        "fields": ["name_kana"],
    },
    "combined_kana": {
        "group": "name",
        "label_ja": "カナ結合",
        "label_en": "Combined kana",
        "description_ja": "姓と名の区切りが消える",
        "description_en": "The separator between family and given name disappears",
        "example_ja": "佐藤 太郎 → 佐藤太郎",
        "example_en": "佐藤 太郎 → 佐藤太郎",
        "fields": ["name_kana"],
    },
    "kana_in_romaji": {
        "group": "name",
        "label_ja": "ローマ字欄へのカナ",
        "label_en": "Kana in romaji",
        "description_ja": "ローマ字欄にカナが入る",
        "description_en": "Kana is entered in the romaji field",
        "example_ja": "Taro Sato → サトウ タロウ",
        "example_en": "Taro Sato → サトウ タロウ",
        "fields": ["name_romaji"],
    },
    "homophone_kanji": {
        "group": "name",
        "label_ja": "同音異字",
        "label_en": "Homophone kanji",
        "description_ja": "読みは同じで漢字だけが異なる",
        "description_en": "Kanji differs while the reading stays the same",
        "example_ja": "サトウ: 佐藤 ↔ 左藤",
        "example_en": "サトウ: 佐藤 ↔ 左藤",
        "fields": ["name_kanji"],
    },
    "kana_form": {
        "group": "name",
        "label_ja": "カナ表記ゆれ",
        "label_en": "Kana form",
        "description_ja": "ひらがな・半角カナへ変換される",
        "description_en": "Kana changes to hiragana or half-width form",
        "example_ja": "サトウ → さとう / ｻﾄｳ",
        "example_en": "サトウ → さとう / ｻﾄｳ",
        "fields": ["name_kana"],
    },
    "digit_transpose": {
        "group": "contact",
        "label_ja": "電話番号の桁入替",
        "label_en": "Digit transpose",
        "description_ja": "電話番号の隣接する数字が入れ替わる",
        "description_en": "Adjacent phone digits are swapped",
        "example_ja": "0901234 → 0901324",
        "example_en": "0901234 → 0901324",
        "fields": ["phone"],
    },
    "email_typo": {
        "group": "contact",
        "label_ja": "メール入力誤り",
        "label_en": "Email typo",
        "description_ja": "メールのローカル部に入力誤りが入る",
        "description_en": "A local-part character is mistyped",
        "example_ja": "taro@example.jp → txro@example.jp",
        "example_en": "taro@example.jp → txro@example.jp",
        "fields": ["email"],
    },
    "shared_contact": {
        "group": "contact",
        "label_ja": "代理店共通連絡先",
        "label_en": "Shared office contact",
        "description_ja": "顧客の代わりに代理店の電話・メールが入る",
        "description_en": "The office phone or email replaces the customer's contact",
        "example_ja": "顧客電話 → office012@example.jp",
        "example_en": "Customer phone → office012@example.jp",
        "fields": ["phone", "email"],
    },
}

QUERY_INFO: dict[str, dict[str, str]] = {
    "existing_stable": {
        "label_ja": "既存・安定",
        "label_en": "Existing stable",
        "description_ja": "変更の少ない既存顧客",
        "description_en": "Existing customer with few changes",
    },
    "existing_changed": {
        "label_ja": "既存・変更",
        "label_en": "Existing changed",
        "description_ja": "一部の項目が変化した既存顧客",
        "description_en": "Existing customer with changed fields",
    },
    "existing_damaged": {
        "label_ja": "既存・破損",
        "label_en": "Existing damaged",
        "description_ja": "複数項目に誤りや欠損がある既存顧客",
        "description_en": "Existing customer with corruption or missing values",
    },
    "new_customer": {
        "label_ja": "新規顧客",
        "label_en": "New customer",
        "description_ja": "参照にはない新規顧客",
        "description_en": "Customer absent from the reference set",
    },
    "related_only": {
        "label_ja": "関係者のみ",
        "label_en": "Related only",
        "description_ja": "同一世帯など近い別人",
        "description_en": "A nearby related person, such as a household member",
    },
    "insufficient": {
        "label_ja": "情報不足",
        "label_en": "Insufficient",
        "description_ja": "照合に必要な情報が少ない入力",
        "description_en": "Input with too little information for resolution",
    },
}


def _effective_availability(noise: NoiseConfig) -> dict[str, dict[str, float]]:
    return {
        channel: {
            field: noise.field_availability.get(channel, {}).get(field, 1.0)
            for field in NOISE_FIELDS
        }
        for channel in CHANNEL_NAMES
    }


def _balanced_counts(total: int, shares: Mapping[str, float]) -> dict[str, int]:
    counts = {name: round(total * share) for name, share in shares.items()}
    first = next(iter(counts))
    counts[first] += total - sum(counts.values())
    return counts


def _time_window(cfg: SyntheticConfig) -> dict[str, Any]:
    snapshot = date.fromisoformat(cfg.snapshot_date)
    query = date.fromisoformat(cfg.query_date)
    return {
        "snapshot_date": cfg.snapshot_date,
        "query_date": cfg.query_date,
        "gap_days": (query - snapshot).days,
    }


def _dataset_impact(
    cfg: SyntheticConfig,
    noise: NoiseConfig,
    records: int,
    quality: Mapping[str, float],
) -> dict[str, Any]:
    quality_counts = _balanced_counts(records, quality)
    quality_rows = [
        {
            "id": name,
            "share": share,
            "records": quality_counts[name],
            "corruption_scale": noise.band_scale[name],
            "residual_missing_rate": noise.missing_rates[name],
            "estimated_missing_records": round(quality_counts[name] * noise.missing_rates[name]),
        }
        for name, share in quality.items()
    ]
    channel_shares = {channel: 1.0 / len(CHANNEL_NAMES) for channel in CHANNEL_NAMES}
    channel_counts = _balanced_counts(records, channel_shares)
    availability = _effective_availability(noise)
    channel_rows = [
        {
            "id": channel,
            "share": channel_shares[channel],
            "records": channel_counts[channel],
            "bundle": list(noise.channel_events.get(channel, ())),
            "availability": availability[channel],
        }
        for channel in CHANNEL_NAMES
    ]
    field_rows = []
    for field in NOISE_FIELDS:
        estimated_rate = sum(
            share
            * channel_shares[channel]
            * (1.0 - availability[channel].get(field, 1.0) * (1.0 - noise.missing_rates[name]))
            for name, share in quality.items()
            for channel in CHANNEL_NAMES
        )
        field_rows.append(
            {
                "id": field,
                "estimated_missing_rate": estimated_rate,
                "estimated_missing_records": round(records * estimated_rate),
            }
        )
    weighted_scale = sum(quality[name] * noise.band_scale[name] for name in quality)
    weighted_residual = sum(quality[name] * noise.missing_rates[name] for name in quality)
    return {
        "quality": quality_rows,
        "channels": channel_rows,
        "fields": field_rows,
        "overall": {
            "weighted_corruption_scale": weighted_scale,
            "weighted_residual_missing_rate": weighted_residual,
            "field_rate_note": (
                "Includes channel availability and residual omission; "
                "event-induced blanks are not counted."
            ),
        },
    }


def controls_from_config(cfg: SyntheticConfig) -> dict[str, Any]:
    noise = noise_for_config(cfg)
    return {
        "profile": cfg.profile,
        "difficulty": cfg.difficulty,
        "seed": cfg.seed,
        "n_parties": cfg.n_parties_override,
        "mean_records": cfg.mean_records,
        "n_agencies": cfg.n_agencies,
        "person_share": cfg.person_share,
        "query_share": cfg.query_share,
        "twin_rate": cfg.twin_rate,
        "poor": cfg.poor,
        "catastrophic": cfg.catastrophic,
        "snapshot_date": cfg.snapshot_date,
        "query_date": cfg.query_date,
        "event_rates": noise.event_rates,
        "band_scale": noise.band_scale,
        "missing_rates": noise.missing_rates,
        "field_availability": _effective_availability(noise),
        "stale_rate": noise.stale_rate,
        "query_mix": cfg.query_mix,
    }


def config_from_controls(raw: Mapping[str, Any]) -> SyntheticConfig:
    """Create the sole validated generator parameter object from API input."""

    def optional_float(key: str) -> float | None:
        value = raw.get(key)
        return None if value is None else float(value)

    return SyntheticConfig(
        seed=int(raw.get("seed", 42)),
        profile=str(raw.get("profile", "smoke")),
        difficulty=str(raw.get("difficulty", "realistic")),
        n_parties_override=int(raw.get("n_parties", 0)),
        mean_records_override=optional_float("mean_records"),
        poor_override=optional_float("poor"),
        catastrophic_override=optional_float("catastrophic"),
        n_agencies=int(raw.get("n_agencies", 18)),
        person_share=float(raw.get("person_share", 0.90)),
        query_share=float(raw.get("query_share", 0.16)),
        twin_rate=float(raw.get("twin_rate", 0.15)),
        snapshot_date=str(raw.get("snapshot_date", "2025-12-31")),
        query_date=str(raw.get("query_date", "2026-08-25")),
        noise_event_rates={str(k): float(v) for k, v in dict(raw.get("event_rates", {})).items()},
        noise_band_scale={str(k): float(v) for k, v in dict(raw.get("band_scale", {})).items()},
        noise_missing_rates={
            str(k): float(v) for k, v in dict(raw.get("missing_rates", {})).items()
        },
        noise_field_availability={
            str(channel): {str(field): float(value) for field, value in dict(values).items()}
            for channel, values in dict(raw.get("field_availability", {})).items()
        },
        noise_stale_rate=optional_float("stale_rate"),
        query_mix={str(k): float(v) for k, v in dict(raw.get("query_mix") or QUERY_MIX).items()},
    )


def catalog() -> dict[str, Any]:
    cfg = SyntheticConfig(profile="smoke", difficulty="realistic")
    noise = noise_for_config(cfg)
    return {
        "profiles": [
            {
                "id": name,
                **PROFILE_INFO[name],
                "n_parties": int(values["n_parties"]),
                "mean_records": float(values["mean_records"]),
            }
            for name, values in PROFILES.items()
        ],
        "difficulties": [
            {
                "id": name,
                **DIFFICULTY_INFO[name],
                "poor": float(values["poor"]),
                "catastrophic": float(values["catastrophic"]),
                "quality_share": {
                    "average": 1.0 - float(values["poor"]),
                    "poor": float(values["poor"]) - float(values["catastrophic"]),
                    "catastrophic": float(values["catastrophic"]),
                },
            }
            for name, values in DIFFICULTY.items()
        ],
        "events": [
            {"id": name, **EVENT_INFO[name], "default_rate": noise.event_rates[name]}
            for name in EVENT_NAMES
        ],
        "channels": [
            {
                "id": channel,
                "availability": _effective_availability(noise)[channel],
                "bundle": list(noise.channel_events.get(channel, ())),
            }
            for channel in CHANNEL_NAMES
        ],
        "query_scenarios": [
            {"id": name, **QUERY_INFO[name], "default_share": QUERY_MIX[name]}
            for name in QUERY_INFO
        ],
        "defaults": controls_from_config(cfg),
        "bounds": {
            "n_parties": {"min": 0, "max": 2_000_000},
            "mean_records": {"min": 1.0, "max": 20.0},
            "n_agencies": {"min": 1, "max": 100},
            "rate": {"min": 0.0, "max": 1.0},
            "band_scale": {"min": 0.0, "max": 3.0},
        },
    }


def preview(raw: Mapping[str, Any]) -> dict[str, Any]:
    cfg = config_from_controls(raw)
    noise = noise_for_config(cfg)
    records = max(cfg.n_parties, round(cfg.n_parties * cfg.mean_records))
    queries = int(cfg.n_parties * cfg.query_share)
    quality = {
        "average": 1.0 - cfg.poor,
        "poor": cfg.poor - cfg.catastrophic,
        "catastrophic": cfg.catastrophic,
    }
    weighted_scale = sum(quality[name] * noise.band_scale[name] for name in quality)
    dataset_impact = _dataset_impact(cfg, noise, records, quality)
    events = []
    stale_multiplier = noise.stale_rate / DEFAULT_STALE_RATE
    for name in EVENT_NAMES:
        rate = noise.event_rates[name]
        stale_factor = stale_multiplier if name in STALE_EVENT_NAMES else 1.0
        effective_rate = min(1.0, rate * weighted_scale * stale_factor)
        events.append(
            {
                "id": name,
                **EVENT_INFO[name],
                "rate": rate,
                "effective_rate": effective_rate,
                "estimated_records": round(records * effective_rate),
            }
        )
    warnings: list[dict[str, str]] = []
    if records > 500_000:
        warnings.append(
            {
                "key": "large_preview",
                "ja": "大規模生成です。ディスク容量と所要時間を確認してください。",
                "en": "This is a large generation; check disk space and duration.",
            }
        )
    if cfg.catastrophic >= 0.25:
        warnings.append(
            {
                "key": "high_catastrophic",
                "ja": "致命的品質の割合が25%を超えています。",
                "en": "Catastrophic-quality records exceed 25%.",
            }
        )
    high_events = [name for name in EVENT_NAMES if noise.event_rates[name] >= 0.5]
    if high_events:
        warnings.append(
            {
                "key": "high_event_rate",
                "ja": f"高強度イベント: {', '.join(high_events)}",
                "en": f"High-intensity events: {', '.join(high_events)}",
            }
        )
    encoded = json.dumps(
        controls_from_config(cfg), ensure_ascii=False, sort_keys=True, default=str
    ).encode()
    return {
        "config_hash": hashlib.sha256(encoded).hexdigest()[:12],
        "controls": controls_from_config(cfg),
        "time_window": _time_window(cfg),
        "dataset_impact": dataset_impact,
        "counts": {
            "parties": cfg.n_parties,
            "records": records,
            "queries": queries,
            "agencies": cfg.n_agencies,
        },
        "quality": quality,
        "weighted_band_scale": weighted_scale,
        "events": events,
        "missingness": {
            "by_quality": noise.missing_rates,
            "estimated_average_rate": sum(quality[k] * noise.missing_rates[k] for k in quality),
        },
        "channels": [
            {
                "id": channel,
                "availability": _effective_availability(noise)[channel],
                "bundle": list(noise.channel_events.get(channel, ())),
            }
            for channel in CHANNEL_NAMES
        ],
        "query_mix": [
            {
                "id": name,
                **QUERY_INFO[name],
                "share": cfg.query_mix[name],
                "estimated_queries": round(queries * cfg.query_mix[name]),
            }
            for name in QUERY_INFO
        ],
        "samples": build_samples(cfg, noise),
        "examples": build_examples(cfg, noise),
        "warnings": warnings,
        "estimate_note": {
            "ja": (
                "件数は期待値です。イベントの重なりとポアソン抽出により"
                "実生成と一致しない場合があります。"
            ),
            "en": (
                "Counts are estimates; event overlap and Poisson sampling can differ "
                "from the generated result."
            ),
        },
    }
