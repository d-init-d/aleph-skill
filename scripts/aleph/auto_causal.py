"""Deterministic auto-causal induction bridge from research evidence to simulation models."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import FORMULA_VERSION
from .import_ledger import import_d_research_ledger
from .io import (
    canonical_hash,
    canonical_json_bytes,
    load_json_secure,
    load_jsonl_secure,
    write_json_atomic,
)
from .paths import path_contains_link_or_reparse
from .schema import parse_timestep_seconds

PROPOSAL_SCHEMA_VERSION = "1.0.0"
REVIEW_SCHEMA_VERSION = "1.0.0"

# --- Diacritic & Slug Utilities ---

def _strip_accents(text: str) -> str:
    """Normalize unicode, transliterate đ/Đ, and strip combining diacritical marks."""
    text = text.replace("đ", "d").replace("Đ", "D")
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _slugify(text: str) -> str:
    """Create deterministic alphanumeric ASCII slug with hyphens."""
    ascii_text = _strip_accents(text).lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    return cleaned or "unnamed"


_COMPACT_STOPWORDS = frozenset(
    {
        "the", "a", "an", "of", "in", "on", "at", "for", "with", "across", "among", "during",
        "các", "những", "việc", "sự", "tại", "trong", "với", "của", "cho",
    }
)


def _compact_factor_phrase(text: str, *, max_words: int = 6, max_chars: int = 56) -> tuple[str, str]:
    """Return ``(compact_name, raw_name)`` capping long clause phrases cleanly."""
    raw_name = re.sub(r"\s+", " ", text).strip(" ,.;:-")
    if not raw_name:
        return "unnamed", "unnamed"
    if len(raw_name) <= max_chars and len(raw_name.split()) <= max_words:
        return raw_name, raw_name

    # Strip subordinate/relative clauses first to retain the core head noun phrase.
    head = re.split(
        r"\b(?:that|which|who|whom|whose|where|when|while|whereas|because|since|although|including|without)\b|"
        r"(?:\bmà\b|\btrong khi\b|\bnhằm\b|\bđể\b|\bbởi vì\b|\bmặc dù\b|\bbao gồm\b)",
        raw_name,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip(" ,.;:-")
    words = head.split() or raw_name.split()
    if len(words) > max_words:
        filtered = [w for w in words if w.lower() not in _COMPACT_STOPWORDS]
        if len(filtered) >= 2:
            words = filtered
    selected: list[str] = []
    total_len = 0
    for word in words[:max_words]:
        projected = total_len + (1 if selected else 0) + len(word)
        if selected and projected > max_chars:
            break
        selected.append(word)
        total_len = projected
    compact = " ".join(selected) if selected else raw_name[:max_chars].strip()
    return compact, raw_name


# --- Pattern Matching for Bilingual Causal Extraction ---

# English causal verbs / phrases
EN_POSITIVE_RE = re.compile(
    r"\b(?:increases?|boosts?|raises?|stimulates?|drives?|amplifies?|accelerates?|expands?|widens?|exacerbates?|aggravates?|escalates?|galvanizes?|mobilizes?|bolsters?|strengthens?|heightens?|leads? to higher|causes? higher|results? in higher)\b",
    re.IGNORECASE,
)
EN_NEGATIVE_RE = re.compile(
    r"\b(?:decreases?|reduces?|lowers?|inhibits?|dampens?|cuts?|suppresses?|erodes?|undermines?|curbs?|slows?(?:\s+down)?|depletes?|disrupts?|degrades?|destabilizes?|weakens?|collapses?|strains?|deters?|constrains?|leads? to lower|causes? lower|results? in lower)\b",
    re.IGNORECASE,
)
EN_CORRELATION_RE = re.compile(
    r"\b(?:is correlated with|are correlated with|correlated with|associated with|co-occurs with|co-occur with|linked with|correlates? with)\b",
    re.IGNORECASE,
)
EN_NEUTRAL_CAUSAL_RE = re.compile(
    r"\b(?:causes?|triggers?|affects?|impacts?|influences?|results? in|contributes? to|leads? to|shapes?)\b",
    re.IGNORECASE,
)

# Vietnamese causal verbs / phrases
VI_POSITIVE_RE = re.compile(
    r"(?:làm tăng|thúc đẩy|nâng cao|kích thích|dẫn đến tăng|tăng cường|khuếch đại|đẩy mạnh|tạo đà cho|gia tăng|củng cố thêm|củng cố|làm trầm trọng|làm leo thang|châm ngòi cho|thổi bùng|huy động|đẩy cao|đẩy(?!\s+lùi)\b)",
    re.IGNORECASE,
)
VI_NEGATIVE_RE = re.compile(
    r"(?:làm giảm|làm suy giảm|suy giảm|kìm hãm|kìm chế|kiềm chế|hạ thấp|cắt giảm|hạn chế|kéo tụt|kéo giảm|đẩy lùi|làm suy yếu|bào mòn|thu hẹp|làm cạn kiệt|làm gián đoạn|phá hủy|làm suy thoái|răn đe|gây bất ổn|bóp nghẹt|cản trở)",
    re.IGNORECASE,
)
VI_CORRELATION_RE = re.compile(
    r"(?:tương quan với|đi kèm với|đồng biến với|nghịch biến với|liên hệ với)",
    re.IGNORECASE,
)
VI_NEUTRAL_CAUSAL_RE = re.compile(
    r"(?:tác động đến|ảnh hưởng đến|gây ra|dẫn tới|dẫn đến|khiến cho|kéo theo|góp phần làm|góp phần|chi phối)",
    re.IGNORECASE,
)

# Negation detection
NEGATION_EN_RE = re.compile(
    r"\b(?:it is false that|false that|is false that|there is no evidence that|no evidence (?:that|of)|does not|do not|did not|not|fails? to|failed to|never|cannot|has no effect on|have no effect on|disproves?|refutes?)\b",
    re.IGNORECASE,
)
NEGATION_VI_RE = re.compile(
    r"(?:sai lầm khi cho rằng|không đúng khi cho rằng|chưa có bằng chứng|không có bằng chứng|không làm|không|chẳng|chưa|không hề|không thể|thất bại trong việc|không có tác động|không ảnh hưởng)",
    re.IGNORECASE,
)

# Mechanism extraction
MECHANISM_EN_RE = re.compile(
    r"\b(?:via|through|by means of|by mechanism of)\s+([A-Za-z0-9_ -]{3,100})(?:[;,.]|$)",
    re.IGNORECASE,
)
MECHANISM_VI_RE = re.compile(
    r"(?:thông qua|bằng cơ chế|nhờ vào|thông qua kênh|bằng cách)\s+([^;,.\n]{3,100})(?:[;,.]|$)",
    re.IGNORECASE,
)

# Lag extraction
LAG_EN_RE = re.compile(
    r"\b(?:with\s+(?:a\s+)?lag\s+of|delay\s+of|lagged\s+by|after|over|within)\s+(\d+)\s*(tick|ticks|day|days|week|weeks|month|months|quarter|quarters|year|years)\b"
    r"|\bwith\s+(?:a\s+)?(\d+)[-\s]+(tick|day|week|month|quarter|year)\s+lag\b",
    re.IGNORECASE,
)
LAG_VI_RE = re.compile(
    r"(?:với\s+độ\s+trễ|độ\s+trễ|trễ|sau|trong\s+vòng)\s+(\d+)\s*(tick|ngày|tuần|tháng|quý|năm)\b",
    re.IGNORECASE,
)

# Effect size / magnitude extraction
EFFECT_SIZE_EN_RE = re.compile(
    r"\b(?:by|effect size (?:of)?|magnitude (?:of)?|coefficient (?:of)?|elasticity (?:of)?)\s*([+-]?\d+(?:\.\d+)?%?)\b",
    re.IGNORECASE,
)
EFFECT_SIZE_VI_RE = re.compile(
    r"(?:mức|khoảng|hệ số|tỷ lệ|quy mô)\s*([+-]?\d+(?:\.\d+)?%?)",
    re.IGNORECASE,
)

# Stock keywords
STOCK_EN_WORDS = {
    "stock", "debt", "inventory", "reserve", "reserves", "accumulated", "wealth", "capital", "holdings",
    "reservoir", "groundwater", "forest", "forest cover", "glacier", "soil moisture", "biomass",
    "munitions", "troop", "troops", "morale", "legitimacy", "trust", "credibility", "cohesion",
}
STOCK_VI_WORDS = {
    "tồn kho", "nợ", "dự trữ", "tích lũy", "vốn", "tài sản",
    "rừng", "độ che phủ rừng", "nước ngầm", "hồ chứa", "độ ẩm đất", "sinh khối",
    "quân số", "sĩ khí", "uy tín", "niềm tin", "đạn dược", "gắn kết",
}

# Flow keywords
FLOW_EN_WORDS = {
    "flow", "rate", "speed", "growth", "deficit", "surplus", "influx", "outflow", "issuance",
    "rainfall", "precipitation", "emission", "emissions", "runoff", "deforestation", "desertification",
    "casualties", "recruitment", "desertion", "mobilization", "bombardment", "aid",
}
FLOW_VI_WORDS = {
    "dòng tiền", "tốc độ", "tỷ lệ tăng", "thâm hụt", "thặng dư", "dòng vốn", "phát hành",
    "lượng mưa", "phát thải", "dòng chảy", "phá rừng", "thương vong", "tuyển quân", "biểu tình", "viện trợ",
}

# Baseline extraction
BASELINE_EN_RE = re.compile(r"\b(?:baseline (?:of)?|initial (?:value)? (?:of)?)\s*([+-]?\d+(?:\.\d+)?)\b", re.IGNORECASE)
BASELINE_VI_RE = re.compile(r"(?:mức cơ sở|giá trị ban đầu)\s*([+-]?\d+(?:\.\d+)?)", re.IGNORECASE)

# Observation time extraction
OBS_TIME_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)?)\b")

# Unit keywords
UNIT_PATTERNS = [
    (re.compile(r"\b(?:percent|percentage|%|điểm phần trăm)\b", re.IGNORECASE), "%"),
    (re.compile(r"\b(?:basis points|bps)\b", re.IGNORECASE), "bps"),
    (re.compile(r"\b(?:usd|dollar|\$)\b", re.IGNORECASE), "USD"),
    (re.compile(r"\b(?:vnd|đồng)\b", re.IGNORECASE), "VND"),
    (re.compile(r"\b(?:triệu usd|million usd)\b", re.IGNORECASE), "M_USD"),
    (re.compile(r"\b(?:tỷ vnd|billion vnd)\b", re.IGNORECASE), "B_VND"),
    (re.compile(r"(?:°c|\bdegrees?\s*c\b|\bcelsius\b|\bđộ\s*c\b)", re.IGNORECASE), "degC"),
    (re.compile(r"\b(?:mm|millimeters?|millimetres?|milimet)\b", re.IGNORECASE), "mm"),
    (re.compile(r"\b(?:ppm)\b", re.IGNORECASE), "ppm"),
    (re.compile(r"\b(?:gtco2|mtco2|tco2|tonnes?\s+co2)\b", re.IGNORECASE), "tCO2"),
    (re.compile(r"\b(?:mwh|mw|gw)\b", re.IGNORECASE), "MW"),
    (re.compile(r"\b(?:troops|soldiers|personnel|binh sĩ)\b", re.IGNORECASE), "personnel"),
]


def _detect_unit(text: str) -> str:
    for regex, unit_name in UNIT_PATTERNS:
        if regex.search(text):
            return unit_name
    return ""


def _classify_scale(name: str) -> str:
    lower = name.lower()
    for word in STOCK_EN_WORDS:
        if re.search(rf"\b{word}\b", lower):
            return "stock"
    for word in STOCK_VI_WORDS:
        if word in lower:
            return "stock"
    for word in FLOW_EN_WORDS:
        if re.search(rf"\b{word}\b", lower):
            return "flow"
    for word in FLOW_VI_WORDS:
        if word in lower:
            return "flow"
    return "level"


def _parse_confidence(raw: Any) -> float:
    if isinstance(raw, (int, float)):
        return max(0.0, min(1.0, float(raw)))
    if isinstance(raw, str):
        val = raw.strip().lower()
        if val == "high":
            return 0.85
        if val == "medium":
            return 0.60
        if val == "low":
            return 0.30
        try:
            parsed = float(val)
            return max(0.0, min(1.0, parsed))
        except ValueError:
            pass
    return 0.50


def _parse_lag_ticks(match: re.Match[str]) -> int:
    groups = [g for g in match.groups() if g is not None]
    if len(groups) < 2:
        return 0
    val = int(groups[0])
    unit = groups[1].lower()
    if unit in ("tick", "ticks"):
        return max(0, val)
    if unit in ("day", "days", "ngày"):
        return max(0, val)
    if unit in ("week", "weeks", "tuần"):
        return max(0, val * 7)
    if unit in ("month", "months", "tháng"):
        return max(0, val * 30)
    if unit in ("quarter", "quarters", "quý"):
        return max(0, val * 90)
    if unit in ("year", "years", "năm"):
        return max(0, val * 365)
    return max(0, val)


def _strip_causal_modifiers(text: str) -> str:
    """Remove trailing/embedded lag, effect-size, and time-horizon modifiers before entity slugification."""
    cleaned = LAG_EN_RE.sub(" ", text)
    cleaned = LAG_VI_RE.sub(" ", cleaned)
    cleaned = EFFECT_SIZE_EN_RE.sub(" ", cleaned)
    cleaned = re.sub(
        r"(?:với\s+|ở\s+)?(?:mức|khoảng|hệ số|tỷ lệ|quy mô)\s*[+-]?\d+(?:\.\d+)?%?",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b(?:over|within|across|in|for)\s+(?:the\s+next\s+)?\d+\s*(?:tick|ticks|day|days|week|weeks|month|months|quarter|quarters|year|years)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"(?:trong|sau|với)\s+(?:khoảng\s+)?\d+\s*(?:tick|ngày|tuần|tháng|quý|năm)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"(?:\s*,?\s*(?:ngay\s+lập\s+tức|immediately))+$",
        "",
        cleaned.strip(),
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.;:-")
    return cleaned


def _parse_effect_size(val_str: str) -> float | None:
    val_str = val_str.strip()
    is_percent = val_str.endswith("%")
    if is_percent:
        val_str = val_str[:-1]
    try:
        num = float(val_str)
        return num / 100.0 if is_percent else num
    except ValueError:
        return None


_COND_EN_RE = re.compile(
    r"^\s*(?P<conj>if|when|whenever|once|unless|provided\s+that|because|since|as)\s+(?P<cond>.+?)(?:,\s*(?:then\s+)?|\s+then\s+)(?P<cons>.+?)\s*$",
    re.IGNORECASE,
)
_COND_VI_RE = re.compile(
    r"^\s*(?P<conj>nếu\s+như|nếu|hễ|khi|mỗi\s+khi|một\s+khi|trừ\s+khi|do|bởi\s+vì|vì)\s+(?P<cond>.+?)(?:,\s*(?:thì\s+|nên\s+)?|\s+(?:thì|nên)\s+)(?P<cons>.+?)\s*$",
    re.IGNORECASE,
)
_CLAUSE_UP_RE = re.compile(
    r"(?:\b(?:rises?|increases?|surges?|spikes?|grows?|expands?|intensifies?|escalates?|prolongs?|strengthens?|worsens?|exceeds?|continues?|persists?|arrives?|reaches?|raises?|high|higher)\b|tăng\s+lên|tăng\s+mạnh|tăng\s+cao|gia\s+tăng|tăng|kéo\s+dài|leo\s+thang|bùng\s+phát|trầm\s+trọng|vượt\s+ngưỡng)",
    re.IGNORECASE,
)
_CLAUSE_DOWN_RE = re.compile(
    r"(?:\b(?:falls?|drops?|declines?|decreases?|shrinks?|contracts?|collapses?|weakens?|erodes?|fails?|stops?|halts?|ends?|low|lower)\b|giảm\s+sâu|sụt\s+giảm|suy\s+giảm|giảm|suy\s+yếu|sụp\s+đổ|cạn\s+kiệt|gián\s+đoạn|ngừng)",
    re.IGNORECASE,
)
_COND_TAIL_VERB_RE = re.compile(
    r"(?:\s+(?:above\s+threshold|exceeds?\s+\S+|rises?|increases?|surges?|spikes?|grows?|expands?|intensifies?|escalates?|prolongs?|strengthens?|worsens?|continues?|persists?|arrives?|reaches?|falls?|drops?|declines?|decreases?|shrinks?|contracts?|collapses?|weakens?|erodes?|fails?|stops?|halts?|ends?|tăng\s+lên|tăng\s+mạnh|tăng\s+cao|tăng|kéo\s+dài|leo\s+thang|bùng\s+phát|trầm\s+trọng|vượt\s+ngưỡng|giảm\s+sâu|sụt\s+giảm|suy\s+giảm|giảm|suy\s+yếu|sụp\s+đổ|cạn\s+kiệt|gián\s+đoạn|ngừng))+$",
    re.IGNORECASE,
)


def _clean_clause_entity(text: str) -> tuple[str, int]:
    cleaned = re.split(
        r"\b(?:via|through|by means of|thông qua|bằng cơ chế|nhờ vào|bằng cách)\b",
        text,
        flags=re.IGNORECASE,
    )[0].strip()
    cleaned = re.split(r"[,;]|(?<!\d)\.(?!\d)", cleaned)[0].strip()
    up_match = _CLAUSE_UP_RE.search(cleaned)
    down_match = _CLAUSE_DOWN_RE.search(cleaned)
    direction = -1 if (down_match and (not up_match or down_match.start() < up_match.start())) else 1
    cleaned = _COND_TAIL_VERB_RE.sub("", cleaned).strip()
    cleaned = re.sub(r"^(?:an?|the|việc|sự|nguồn|mức|tình\s+trạng)\s+", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = _strip_causal_modifiers(cleaned)
    return cleaned, direction


def _extract_conditional_relation(claim_text: str) -> tuple[str, str, int, str, bool] | None:
    match = _COND_EN_RE.match(claim_text) or _COND_VI_RE.match(claim_text)
    if not match:
        return None
    conj = match.group("conj").strip().lower()
    cond_raw = match.group("cond").strip()
    cons_raw = match.group("cons").strip()
    # Skip negative meta-claims ("If false that...") or nested multi-clause prose
    if len(cond_raw.split()) > 14 or len(cons_raw.split()) > 16:
        return None
    src_name, src_dir = _clean_clause_entity(cond_raw)
    tgt_name, tgt_dir = _clean_clause_entity(cons_raw)
    if not src_name or not tgt_name:
        return None
    if conj in {"unless", "trừ khi"}:
        src_dir = -src_dir
    net_sign = 1 if (src_dir * tgt_dir) >= 0 else -1
    rel = "increases" if net_sign == 1 else "decreases"
    return src_name, tgt_name, net_sign, rel, False


def _extract_entities_and_relation(claim_text: str) -> tuple[str, str, int | None, str, bool]:
    """Extract source entity, target entity, sign (-1, 1 or None), relation label, and is_correlation flag."""
    cond_res = _extract_conditional_relation(claim_text)
    if cond_res is not None:
        return cond_res
    is_correlation = bool(EN_CORRELATION_RE.search(claim_text) or VI_CORRELATION_RE.search(claim_text))
    sign: int | None = None
    relation = "causes"

    # Search for positive
    pos_match = EN_POSITIVE_RE.search(claim_text) or VI_POSITIVE_RE.search(claim_text)
    neg_match = EN_NEGATIVE_RE.search(claim_text) or VI_NEGATIVE_RE.search(claim_text)
    neut_match = EN_NEUTRAL_CAUSAL_RE.search(claim_text) or VI_NEUTRAL_CAUSAL_RE.search(claim_text)

    split_span: tuple[int, int] | None = None
    if pos_match:
        sign = 1
        relation = "increases"
        split_span = pos_match.span()
    elif neg_match:
        sign = -1
        relation = "decreases"
        split_span = neg_match.span()
    elif is_correlation:
        corr_match = EN_CORRELATION_RE.search(claim_text) or VI_CORRELATION_RE.search(claim_text)
        if corr_match:
            split_span = corr_match.span()
        sign = 1
        relation = "correlation"
    elif neut_match:
        sign = 1
        relation = "causes"
        split_span = neut_match.span()

    if split_span:
        source_part = claim_text[: split_span[0]].strip()
        target_part = claim_text[split_span[1] :].strip()
        # Cut off mechanism clauses from target
        target_part = re.split(
            r"\b(?:via|through|by means of|thông qua|bằng cơ chế|nhờ vào|bằng cách)\b",
            target_part,
            flags=re.IGNORECASE,
        )[0].strip()
        # Cut off secondary clauses at comma, semicolon, or non-decimal period
        target = re.split(r"[,;]|(?<!\d)\.(?!\d)", target_part)[0].strip()
        # Clean up leading/trailing prepositions, articles, or negation prefixes
        clean_source = re.sub(
            r"^(?:it is false that|false that|is false that|there is no evidence that|no evidence (?:that|of)|sai lầm khi cho rằng|không đúng khi cho rằng)\s+",
            "",
            source_part,
            flags=re.IGNORECASE,
        ).strip()
        source = re.sub(r"^(an?|the|việc|sự|higher|lower)\s+", "", clean_source, flags=re.IGNORECASE).strip()
        target = re.sub(r"^(an?|the|việc|sự|đến|to|higher|lower)\s+", "", target, flags=re.IGNORECASE).strip()
        source = _strip_causal_modifiers(source)
        target = _strip_causal_modifiers(target)
        source = re.sub(r"\s+(?:tăng mạnh|giảm mạnh|tăng cao|giảm sâu|tăng|giảm)$", "", source, flags=re.IGNORECASE).strip()
        target = re.sub(r"\s+(?:tăng mạnh|giảm mạnh|tăng cao|giảm sâu|tăng|giảm)$", "", target, flags=re.IGNORECASE).strip()
        if source and target:
            return source, target, sign, relation, is_correlation

    # No causal or correlation syntax found: reject fabrication of source-factor / target-factor
    return "", "", None, "non_causal", False


def _extract_structured_or_text_claim(
    row: dict[str, Any], claim_text: str
) -> tuple[str, str, int | None, str, bool]:
    """Prefer structured claim entity/polarity metadata when provided, else parse bilingual text."""
    raw_source = (
        row.get("source_factor")
        or row.get("subject")
        or row.get("cause")
        or row.get("source")
    )
    raw_target = (
        row.get("target_factor")
        or row.get("object")
        or row.get("effect")
        or row.get("target")
    )
    if isinstance(raw_source, str) and raw_source.strip() and isinstance(raw_target, str) and raw_target.strip():
        source_name = raw_source.strip()
        target_name = raw_target.strip()
        _, _, text_sign, text_relation, text_corr = _extract_entities_and_relation(claim_text)
        raw_corr = row.get("is_correlation")
        is_correlation = bool(raw_corr) if raw_corr is not None else text_corr
        raw_pol = row.get("sign", row.get("polarity"))
        sign: int | None
        if raw_pol in (-1, 1) and not isinstance(raw_pol, bool):
            sign = int(raw_pol)
        elif isinstance(raw_pol, str):
            norm_pol = raw_pol.strip().lower()
            if norm_pol in {"+", "pos", "positive", "increase", "increases", "up"}:
                sign = 1
            elif norm_pol in {"-", "neg", "negative", "decrease", "decreases", "down"}:
                sign = -1
            else:
                sign = text_sign if text_sign is not None else 1
        else:
            sign = text_sign if text_sign is not None else 1
        raw_relation = row.get("relation")
        if isinstance(raw_relation, str) and raw_relation.strip():
            relation = raw_relation.strip().lower()
        elif is_correlation:
            relation = "correlation"
        elif sign == -1:
            relation = "decreases"
        elif text_relation in {"increases", "decreases", "causes"}:
            relation = text_relation
        else:
            relation = "increases" if sign == 1 else "causes"
        return source_name, target_name, sign, relation, is_correlation
    return _extract_entities_and_relation(claim_text)



# --- Core Pipeline Functions ---

_TIME_COLUMN_NAMES = frozenset({"period", "date", "month", "year", "time", "timestamp", "tick", "t"})
_TIME_VALUE_RE = re.compile(r"^\d{4}(?:-\d{2}(?:-\d{2})?)?$|^\d+$")


def _infer_series_unit(col_name: str) -> str:
    low = col_name.strip().lower()
    if any(tok in low for tok in ("pct", "percent", "rate", "share", "unemployment", "yield_pct", "cpi_yoy")):
        return "%"
    if any(tok in low for tok in ("bps", "spread")):
        return "bps"
    if any(tok in low for tok in ("index", "idx", "cpi")):
        return "index"
    if any(tok in low for tok in ("usd", "price", "brent", "oil", "cost", "gdp_usd")):
        return "USD"
    if any(tok in low for tok in ("_mm", "rain", "precip")):
        return "mm"
    if any(tok in low for tok in ("temp", "celsius", "degc")):
        return "degC"
    if any(tok in low for tok in ("co2", "ppm")):
        return "ppm"
    return "units"


def _normalise_csv_timestamp(raw_time: str) -> str:
    s = raw_time.strip()
    if re.fullmatch(r"\d{4}-\d{2}", s):
        return f"{s}-01"
    if re.fullmatch(r"\d{4}", s):
        return f"{s}-01-01"
    return s


def _pearson_and_beta(xs: list[float], ys: list[float]) -> tuple[float, float] | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    n = float(len(xs))
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    var_x = sum((x - mx) * (x - mx) for x in xs)
    var_y = sum((y - my) * (y - my) for y in ys)
    if var_x <= 1e-12 or var_y <= 1e-12:
        return None
    r = cov / math.sqrt(var_x * var_y)
    beta = cov / var_x
    if not (math.isfinite(r) and math.isfinite(beta)):
        return None
    return r, beta


def _ingest_wide_timeseries_csv(
    rows: list[dict[str, Any]],
    fieldnames: list[str],
) -> list[dict[str, Any]] | None:
    """Convert a wide numeric CSV table into empirical correlation claim rows.

    Returns None when the CSV does not match a wide numeric time-series layout.
    Every derived edge is explicitly flagged `is_correlation=True` so review rejects
    or holds it for human causal-direction adjudication (`correlation_not_causation`).
    """
    if len(rows) < 3 or len(fieldnames) < 3:
        return None
    lower_fields = {f.strip().lower(): f for f in fieldnames if f}
    if "claim" in lower_fields:
        return None

    time_col: str | None = None
    for field in fieldnames:
        if field and field.strip().lower() in _TIME_COLUMN_NAMES:
            time_col = field
            break
    if time_col is None and fieldnames[0]:
        first_vals = [str(r.get(fieldnames[0]) or "").strip() for r in rows]
        if all(val and _TIME_VALUE_RE.match(val) for val in first_vals):
            time_col = fieldnames[0]
    if time_col is None:
        return None

    series_cols: list[str] = []
    series_values: dict[str, list[float]] = {}
    time_vals: list[str] = [str(r.get(time_col) or "").strip() for r in rows]
    if not all(time_vals):
        return None

    for col in fieldnames:
        if not col or col == time_col:
            continue
        parsed_col: list[float] = []
        valid = True
        for row in rows:
            raw_v = str(row.get(col) or "").strip()
            if not raw_v:
                valid = False
                break
            try:
                num = float(raw_v)
            except ValueError:
                valid = False
                break
            if not math.isfinite(num):
                valid = False
                break
            parsed_col.append(num)
        if valid and len(parsed_col) == len(rows):
            series_cols.append(col)
            series_values[col] = parsed_col

    if len(series_cols) < 2:
        return None

    deltas: dict[str, list[float]] = {
        col: [vals[idx] - vals[idx - 1] for idx in range(1, len(vals))]
        for col, vals in series_values.items()
    }
    obs_time_last = _normalise_csv_timestamp(time_vals[-1])
    obs_time_first = time_vals[0]

    selected_pairs: list[tuple[str, str, int, float, float, int]] = []
    fallback_best: tuple[str, str, int, float, float, int] | None = None
    for i, col_a in enumerate(series_cols):
        da = deltas[col_a]
        for j, col_b in enumerate(series_cols):
            if i == j:
                continue
            db = deltas[col_b]
            best_lag1 = _pearson_and_beta(da[:-1], db[1:]) if len(da) >= 3 else None
            best_lag0 = _pearson_and_beta(da, db) if len(da) >= 2 else None
            if best_lag1 is not None and abs(best_lag1[0]) >= 0.45:
                cand = (col_a, col_b, 1, best_lag1[0], best_lag1[1], len(da) - 1)
                selected_pairs.append(cand)
                if fallback_best is None or abs(cand[3]) > abs(fallback_best[3]):
                    fallback_best = cand
                continue
            if best_lag0 is not None:
                cand0 = (col_a, col_b, 0, best_lag0[0], best_lag0[1], len(da))
                if fallback_best is None or abs(cand0[3]) > abs(fallback_best[3]):
                    fallback_best = cand0
                if i < j and abs(best_lag0[0]) >= 0.55:
                    selected_pairs.append(cand0)

    if not selected_pairs and fallback_best is not None:
        selected_pairs.append(fallback_best)
    if not selected_pairs:
        return None

    derived_rows: list[dict[str, Any]] = []
    for col_a, col_b, lag_ticks, r_val, beta_val, n_pairs in selected_pairs:
        name_a = col_a.replace("_", " ").strip()
        name_b = col_b.replace("_", " ").strip()
        slug_a = _slugify(name_a)
        slug_b = _slugify(name_b)
        src_id = f"factor:{slug_a}"
        tgt_id = f"factor:{slug_b}"
        vals_a = series_values[col_a]
        vals_b = series_values[col_b]
        sign = 1 if r_val >= 0 else -1
        derived_rows.append(
            {
                "claim_id": f"claim:csv-{slug_a}-to-{slug_b}-lag{lag_ticks}",
                "evidence_id": f"evidence:csv-{slug_a}-{slug_b}",
                "record_type": "claim",
                "claim": f"{name_a} is correlated with {name_b}",
                "source_factor": name_a,
                "target_factor": name_b,
                "sign": sign,
                "relation": "correlation",
                "is_correlation": True,
                "lag_ticks": lag_ticks,
                "effect_size": round(abs(beta_val), 6),
                "confidence": min(0.85, round(0.35 + 0.5 * abs(r_val), 3)),
                "mechanism": (
                    f"Empirical delta correlation (r={r_val:.3f}, beta={beta_val:.4f}, "
                    f"lag={lag_ticks}, n={n_pairs}) between '{col_a}' and '{col_b}'"
                ),
                "observation_time": obs_time_last,
                "node_measurements": {
                    src_id: {
                        "baseline": vals_a[0],
                        "initial_value": vals_a[0],
                        "time": _normalise_csv_timestamp(obs_time_first),
                        "unit": _infer_series_unit(col_a),
                    },
                    tgt_id: {
                        "baseline": vals_b[0],
                        "initial_value": vals_b[0],
                        "time": _normalise_csv_timestamp(obs_time_first),
                        "unit": _infer_series_unit(col_b),
                    },
                },
                "quote_or_value": (
                    f"Wide CSV {len(rows)} periods ({obs_time_first}..{time_vals[-1]}): "
                    f"delta r={r_val:.3f}, beta={beta_val:.4f}"
                ),
            }
        )
    return derived_rows


def propose_model(
    evidence_input: list[dict[str, Any]] | Path | str,
    *,
    proposal_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Generate a deterministic causal proposal and gap report from imported evidence."""
    # 1. Resolve evidence rows
    raw_rows: list[dict[str, Any]] = []
    if isinstance(evidence_input, (str, Path)):
        path = Path(evidence_input)
        if path.suffix.lower() == ".csv":
            import_res = import_d_research_ledger(path)
            if import_res.get("ok") and isinstance(import_res.get("evidence_rows"), list):
                raw_rows = import_res["evidence_rows"]
            else:
                with path.open("r", encoding="utf-8-sig", newline="") as handle:
                    reader = csv.DictReader(handle)
                    ordered_fields = list(reader.fieldnames or [])
                    fieldnames = set(ordered_fields)
                    csv_rows = [dict(row) for row in reader if isinstance(row, dict)]
                    if "claim" in fieldnames:
                        raw_rows = csv_rows
                    else:
                        wide_rows = _ingest_wide_timeseries_csv(csv_rows, ordered_fields)
                        if wide_rows is None:
                            raise ValueError(
                                f"CSV ledger must match D-Research/Aleph schema, contain a 'claim' column, or be a wide numeric time-series: {import_res.get('issues')}"
                            )
                        raw_rows = wide_rows
        elif path.suffix.lower() == ".jsonl":
            rows, jsonl_issues = load_jsonl_secure(path)
            if jsonl_issues:
                raise ValueError(f"Invalid JSONL ledger {path}: {jsonl_issues[0].message}")
            raw_rows = [item for item in rows if isinstance(item, dict)]
        elif path.suffix.lower() == ".json":
            data, _ = load_json_secure(path)
            if isinstance(data, list):
                raw_rows = data
            elif isinstance(data, dict):
                candidate_rows = data.get("evidence_rows", data.get("records", []))
                if not isinstance(candidate_rows, list):
                    raise ValueError("Evidence rows must be a JSON array")
                raw_rows = [item for item in candidate_rows if isinstance(item, dict)]
    else:
        raw_rows = evidence_input

    # Filter strictly to record_type == "claim" (P3.A02)
    claim_rows = [
        row for row in raw_rows
        if str(row.get("record_type", "claim")).strip().lower() == "claim"
    ]

    # Calculate deterministic evidence_hash
    evidence_canonical_bytes = canonical_json_bytes(
        sorted(claim_rows, key=lambda r: str(r.get("evidence_id") or r.get("claim_id") or ""))
    )
    evidence_hash = f"sha256:{hashlib.sha256(evidence_canonical_bytes).hexdigest()}"

    if not proposal_id:
        proposal_id = f"proposal:{hashlib.sha256(evidence_canonical_bytes).hexdigest()[:16]}"
    if not created_at:
        created_at = datetime.now(timezone.utc).isoformat()

    candidate_nodes: list[dict[str, Any]] = []
    candidate_edges: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    entity_mappings: dict[str, dict[str, Any]] = {}

    nodes_by_id: dict[str, dict[str, Any]] = {}
    edges_by_id: dict[str, dict[str, Any]] = {}
    gap_count = 0

    # Pass 1: Parse claims into nodes, edges, and gaps
    for row in claim_rows:
        claim_id = str(row.get("claim_id") or row.get("evidence_id") or f"claim:{len(entity_mappings)+1}")
        evidence_id = str(row.get("evidence_id") or claim_id)
        claim_text = str(row.get("claim") or "")
        quote_text = str(row.get("quote_or_value") or row.get("quote_or_anchor") or row.get("evidence") or "")
        combined_text = f"{claim_text}. {quote_text}".strip()
        confidence = _parse_confidence(row.get("confidence"))

        source_name, target_name, sign, relation, is_corr = _extract_structured_or_text_claim(row, claim_text)
        if not source_name or not target_name or relation == "non_causal" or sign not in (-1, 1):
            entity_mappings[claim_id] = {
                "claim_id": claim_id,
                "evidence_ids": [evidence_id],
                "node_ids": [],
                "edge_ids": [],
                "source_anchor": quote_text[:120] if quote_text else claim_text[:120],
            }
            continue
        source_name, raw_source_name = _compact_factor_phrase(source_name)
        target_name, raw_target_name = _compact_factor_phrase(target_name)
        source_slug = _slugify(source_name)
        target_slug = _slugify(target_name)

        source_id = f"factor:{source_slug}"
        target_id = f"factor:{target_slug}"
        edge_id = f"causal:{source_slug}-to-{target_slug}"

        # Register or update candidate nodes
        for node_id, node_name, raw_node_name in [
            (source_id, source_name, raw_source_name),
            (target_id, target_name, raw_target_name),
        ]:
            if node_id not in nodes_by_id:
                scale = _classify_scale(node_name)
                unit = _detect_unit(combined_text)
                measurements = row.get("node_measurements", {})
                candidate_measurement = measurements.get(node_id, measurements.get(node_name, {})) if isinstance(measurements, dict) else {}
                measurement: dict[str, Any] = candidate_measurement if isinstance(candidate_measurement, dict) else {}
                baseline_val = measurement.get("baseline")
                node_status = "inference"
                if not _finite_number(baseline_val):
                    baseline_val = None
                    node_status = "assumption"
                    gap_count += 1
                    gap_id = f"gap:{gap_count}"
                    gaps.append({
                        "gap_id": gap_id,
                        "target_id": node_id,
                        "gap_type": "missing_baseline",
                        "description": f"Baseline value missing for node '{node_name}'. Requires explicit assumption or empirical measurement.",
                    })

                obs_time = (
                    measurement.get("time") or row.get("observation_time")
                    or row.get("observed_at")
                )
                if not obs_time:
                    time_match = OBS_TIME_RE.search(combined_text)
                    if time_match:
                        obs_time = time_match.group(1)

                if obs_time:
                    node_time = str(obs_time)
                else:
                    node_time = None
                    node_status = "assumption"
                    gap_count += 1
                    gap_id = f"gap:{gap_count}"
                    gaps.append({
                        "gap_id": gap_id,
                        "target_id": node_id,
                        "gap_type": "missing_observation_time",
                        "description": f"Observation time missing for node '{node_name}'. Requires explicit timestamp or empirical anchor.",
                    })

                unit = measurement.get("unit", unit)
                if not unit:
                    node_status = "assumption"
                    gap_count += 1
                    gaps.append({"gap_id": f"gap:{gap_count}", "target_id": node_id,
                                 "gap_type": "missing_unit", "description": "An explicit measurement unit is required."})
                node_obj: dict[str, Any] = {
                    "id": node_id,
                    "name": node_name.strip(),
                    "scale": scale,
                    "unit": unit,
                    "baseline": baseline_val,
                    "time": node_time,
                    "status": node_status,
                    "confidence": confidence,
                    "evidence_ids": [evidence_id],
                    "source_anchor": quote_text[:120] if quote_text else claim_text[:120],
                }
                if raw_node_name != node_name:
                    node_obj["description"] = raw_node_name
                if _finite_number(measurement.get("initial_value")):
                    node_obj["initial_value"] = float(measurement["initial_value"])
                if scale == "stock":
                    retention = measurement.get("retention")
                    node_obj["retention"] = retention if isinstance(retention, (int, float)) and _finite_number(retention) and 0 <= retention <= 1 else None
                    if node_obj["retention"] is None:
                        node_obj["status"] = "assumption"
                        gap_count += 1
                        gaps.append({"gap_id": f"gap:{gap_count}", "target_id": node_id,
                                     "gap_type": "missing_retention", "description": "Stock retention must be measured or explicitly reviewed; no default is inferred."})
                nodes_by_id[node_id] = node_obj
            else:
                if evidence_id not in nodes_by_id[node_id]["evidence_ids"]:
                    nodes_by_id[node_id]["evidence_ids"].append(evidence_id)

        # Mechanism extraction (structured field preferred over text regex)
        mechanism: str | None = None
        raw_mech = row.get("mechanism")
        if isinstance(raw_mech, str) and raw_mech.strip():
            mechanism = raw_mech.strip()
        else:
            mech_match = MECHANISM_EN_RE.search(combined_text) or MECHANISM_VI_RE.search(combined_text)
            if mech_match:
                mechanism = mech_match.group(1).strip()

        # Lag extraction (structured field preferred over text regex)
        lag_ticks: int | None = None
        raw_lag = row.get("lag_ticks")
        if isinstance(raw_lag, int) and not isinstance(raw_lag, bool) and raw_lag >= 0:
            lag_ticks = int(raw_lag)
        else:
            lag_match = LAG_EN_RE.search(combined_text) or LAG_VI_RE.search(combined_text)
            if lag_match:
                lag_ticks = _parse_lag_ticks(lag_match)

        # Effect size extraction (structured field preferred over text regex)
        effect_size: float | None = None
        raw_eff = row.get("effect_size")
        if isinstance(raw_eff, (int, float)) and not isinstance(raw_eff, bool) and math.isfinite(float(raw_eff)):
            effect_size = float(raw_eff)
        else:
            eff_match = EFFECT_SIZE_EN_RE.search(combined_text) or EFFECT_SIZE_VI_RE.search(combined_text)
            if eff_match:
                effect_size = _parse_effect_size(eff_match.group(1))

        # Check gaps for this edge
        edge_gap_refs: list[str] = []

        if is_corr:
            gap_count += 1
            gap_id = f"gap:{gap_count}"
            gaps.append({
                "gap_id": gap_id,
                "target_id": edge_id,
                "gap_type": "correlation_not_causation",
                "description": f"Empirical claim reports correlation without establishing causal direction: '{claim_text}'",
            })
            edge_gap_refs.append(gap_id)

        if not mechanism:
            gap_count += 1
            gap_id = f"gap:{gap_count}"
            gaps.append({
                "gap_id": gap_id,
                "target_id": edge_id,
                "gap_type": "missing_mechanism",
                "description": f"No transmission mechanism identified in claim text for '{source_name} -> {target_name}'",
            })
            edge_gap_refs.append(gap_id)

        if lag_ticks is None:
            gap_count += 1
            gap_id = f"gap:{gap_count}"
            gaps.append({
                "gap_id": gap_id,
                "target_id": edge_id,
                "gap_type": "unknown_lag",
                "description": f"Temporal lag unknown between '{source_name}' and '{target_name}'. Cannot infer lag from publishing date.",
            })
            edge_gap_refs.append(gap_id)

        if effect_size is None:
            gap_count += 1
            gap_id = f"gap:{gap_count}"
            gaps.append({
                "gap_id": gap_id,
                "target_id": edge_id,
                "gap_type": "missing_effect_size",
                "description": f"Numerical effect size / elasticity missing for '{source_name} -> {target_name}'",
            })
            edge_gap_refs.append(gap_id)

        # Contradiction detection
        contra = str(row.get("contradiction") or row.get("contradiction_status") or "").strip()
        if contra and contra.lower() not in ("none", "false", "no", "uncontradicted"):
            gap_count += 1
            gap_id = f"gap:{gap_count}"
            gaps.append({
                "gap_id": gap_id,
                "target_id": edge_id,
                "gap_type": "contradiction",
                "description": f"Evidence ledger records contradiction for claim '{claim_id}': {contra}",
            })
            edge_gap_refs.append(gap_id)

        # Negation detection
        is_negated = bool(NEGATION_EN_RE.search(claim_text) or NEGATION_VI_RE.search(claim_text))
        if is_negated:
            gap_count += 1
            gap_id = f"gap:{gap_count}"
            gaps.append({
                "gap_id": gap_id,
                "target_id": edge_id,
                "gap_type": "negated_causal_statement",
                "description": f"Empirical claim reports negation or non-causal statement: '{claim_text}'",
            })
            edge_gap_refs.append(gap_id)

        # Blocked / prohibited source detection
        is_blocked = (
            str(row.get("retrieval_status", "")).strip().lower() in ("blocked", "denied", "prohibited", "error")
            or str(row.get("discovery_disposition", "")).strip().lower() in ("blocked", "denied", "prohibited")
            or "blocked" in str(row.get("reporting_disposition", "")).strip().lower()
        )
        if is_blocked:
            gap_count += 1
            gap_id = f"gap:{gap_count}"
            gaps.append({
                "gap_id": gap_id,
                "target_id": edge_id,
                "gap_type": "blocked_source",
                "description": f"Evidence source for claim '{claim_id}' is marked blocked or prohibited",
            })
            edge_gap_refs.append(gap_id)

        # Determine edge status
        edge_status = "incomplete" if edge_gap_refs else "proposed"

        edge_obj: dict[str, Any] = {
            "id": edge_id,
            "from": source_id,
            "to": target_id,
            "relation": relation,
            "sign": sign if sign in (-1, 1) else 1,
            "status": edge_status,
            "evidence_confidence": confidence,
            "evidence_ids": [evidence_id],
        }
        if mechanism:
            edge_obj["mechanism"] = mechanism
        if lag_ticks is not None:
            edge_obj["lag_ticks"] = lag_ticks
        if effect_size is not None:
            edge_obj["effect_size"] = effect_size
            edge_obj["base_strength"] = abs(effect_size)
        if edge_gap_refs:
            edge_obj["gap_refs"] = edge_gap_refs

        if edge_id in edges_by_id:
            existing = edges_by_id[edge_id]
            if existing["sign"] != sign:
                gap_count += 1
                gap_id = f"gap:{gap_count}"
                gaps.append({
                    "gap_id": gap_id,
                    "target_id": edge_id,
                    "gap_type": "contradiction",
                    "description": f"Conflicting signs proposed for edge {edge_id}: existing {existing['sign']} vs new {sign}",
                })
                existing.setdefault("gap_refs", []).append(gap_id)
                existing["status"] = "incomplete"
                edge_gap_refs.append(gap_id)
            if evidence_id not in existing["evidence_ids"]:
                existing["evidence_ids"].append(evidence_id)
            if edge_gap_refs:
                for gid in edge_gap_refs:
                    if gid not in existing.setdefault("gap_refs", []):
                        existing["gap_refs"].append(gid)
                existing["status"] = "incomplete"
        else:
            edges_by_id[edge_id] = edge_obj

        # Record entity mapping
        entity_mappings[claim_id] = {
            "claim_id": claim_id,
            "evidence_ids": [evidence_id],
            "node_ids": [source_id, target_id],
            "edge_ids": [edge_id],
            "source_anchor": quote_text[:120] if quote_text else claim_text[:120],
        }


    candidate_nodes = list(nodes_by_id.values())
    candidate_edges = list(edges_by_id.values())

    return {
        "schema_version": PROPOSAL_SCHEMA_VERSION,
        "proposal_id": proposal_id,
        "created_at": created_at,
        "evidence_hash": evidence_hash,
        "candidate_nodes": candidate_nodes,
        "candidate_edges": candidate_edges,
        "gaps": gaps,
        "entity_mappings": entity_mappings,
    }


def review_model(
    proposal: dict[str, Any],
    reviewer: str = "deterministic:causal-screening",
    *,
    decisions: dict[str, Any] | None = None,
    reviewed_at: str | None = None,
) -> dict[str, Any]:
    """Examine causal candidate proposal and output cryptographically-bound admission decisions."""
    _validate_sidecar(proposal, "causal-proposal")
    proposal_hash = f"sha256:{canonical_hash(proposal)}"
    evidence_hash = proposal.get("evidence_hash", "")
    review_id = f"review:{proposal.get('proposal_id', '').split(':')[-1]}"

    if not reviewed_at:
        reviewed_at = datetime.now(timezone.utc).isoformat()

    edge_decisions: dict[str, dict[str, Any]] = {}
    node_decisions: dict[str, dict[str, Any]] = {}

    if decisions is not None and (not isinstance(decisions, dict) or not isinstance(reviewer, str) or not reviewer.startswith(("human:", "agent:")) or not reviewer.split(":", 1)[1].strip()):
        raise ValueError("MANUAL_REVIEW_IDENTITY: explicit decisions require a named human or agent reviewer")
    manual_edge_decisions = (decisions or {}).get("edge_decisions", {})
    manual_node_decisions = (decisions or {}).get("node_decisions", {})
    for supplied in (manual_edge_decisions, manual_node_decisions):
        if not isinstance(supplied, dict) or any(not isinstance(item, dict) or not isinstance(item.get("rationale"), str) or not item["rationale"].strip() for item in supplied.values()):
            raise ValueError("INVALID_REVIEW_DECISIONS: decisions require objects and nonempty rationales")
    admitted_count = 0
    incomplete_count = 0
    rejected_count = 0

    gap_by_id = {g["gap_id"]: g for g in proposal.get("gaps", [])}

    # Review edges
    for edge in proposal.get("candidate_edges", []):
        edge_id = edge["id"]
        evidence_refs = edge.get("evidence_ids", [])

        if edge_id in manual_edge_decisions:
            dec = manual_edge_decisions[edge_id]
            edge_decisions[edge_id] = {
                "decision": dec.get("decision", "incomplete"),
                "rationale": dec.get("rationale", "Manual agent decision."),
                "evidence_refs": evidence_refs,
            }
            if "modifications" in dec:
                edge_decisions[edge_id]["modifications"] = dec["modifications"]
        else:
            # Deterministic evaluation based on gaps and candidate completeness
            gaps = edge.get("gap_refs", [])
            gap_objs = [gap_by_id[gid] for gid in gaps if gid in gap_by_id]
            status = edge.get("status", "incomplete")
            if gaps or status == "incomplete":
                incomplete_count += 1
                decision_type = "incomplete"
                # If gap is correlation, contradiction, negation, or blocked source, reject from causal graph
                has_corr_gap = any(g.get("gap_type") == "correlation_not_causation" for g in gap_objs)
                has_contra_gap = any(g.get("gap_type") == "contradiction" for g in gap_objs)
                has_neg_gap = any(g.get("gap_type") == "negated_causal_statement" for g in gap_objs)
                has_blocked_gap = any(g.get("gap_type") == "blocked_source" for g in gap_objs)
                if has_corr_gap or has_contra_gap or has_neg_gap or has_blocked_gap:
                    decision_type = "reject"
                    rejected_count += 1
                    incomplete_count -= 1
                    rationale = f"Rejected: Causal edge blocked by critical gap (correlation, contradiction, negation, or blocked source): {gaps}"
                else:
                    rationale = f"Incomplete: Missing required parameters or mechanisms: {gaps}"

                edge_decisions[edge_id] = {
                    "decision": decision_type,
                    "rationale": rationale,
                    "evidence_refs": evidence_refs,
                }

            else:
                admitted_count += 1
                edge_decisions[edge_id] = {
                    "decision": "admit",
                    "rationale": "Admitted: Grounded causal mechanism, lag, sign, and effect size present in empirical evidence.",
                    "evidence_refs": evidence_refs,
                }

    # Review nodes
    for node in proposal.get("candidate_nodes", []):
        node_id = node["id"]
        if node_id in manual_node_decisions:
            node_decisions[node_id] = manual_node_decisions[node_id]
        else:
            # If connected to any admitted edge, admit node
            connected = any(
                (edge["from"] == node_id or edge["to"] == node_id)
                and edge_decisions.get(edge["id"], {}).get("decision") in ("admit", "revise")
                for edge in proposal.get("candidate_edges", [])
            )
            if connected:
                node_decisions[node_id] = {
                    "decision": "admit",
                    "rationale": f"Node connected to admitted causal transmission path ({node.get('scale', 'level')}).",
                }
            else:
                node_decisions[node_id] = {
                    "decision": "reject" if rejected_count > 0 and admitted_count == 0 else "revise",
                    "rationale": "Node not connected to any admitted transmission edge.",
                }

    counts = [d.get("decision") for d in edge_decisions.values()]
    admitted_count = sum(d in ("admit", "revise") for d in counts)
    incomplete_count = counts.count("incomplete")
    rejected_count = counts.count("reject")
    complete_manual_review = decisions is not None and all(
        item["id"] in supplied and isinstance(supplied[item["id"]], dict)
        and supplied[item["id"]].get("rationale", "").strip()
        for items, supplied in ((proposal.get("candidate_nodes", []), manual_node_decisions),
                                (proposal.get("candidate_edges", []), manual_edge_decisions)) for item in items
    )
    for decision in [*edge_decisions.values(), *node_decisions.values()]:
        if decision.get("decision") not in {"admit", "revise", "reject", "incomplete"}:
            raise ValueError("INVALID_REVIEW_DECISION")
    has_assumptions = any(n.get("status") == "assumption" for n in proposal.get("candidate_nodes", []))
    has_gaps = bool(proposal.get("gaps"))
    has_unadmitted_nodes = any(d.get("decision") not in {"admit", "revise"} for d in node_decisions.values())
    if admitted_count > 0 and (incomplete_count > 0 or rejected_count > 0 or has_assumptions or has_gaps):
        overall_status = "partial"
    elif admitted_count > 0 and incomplete_count == 0 and rejected_count == 0 and not has_assumptions and not has_gaps and not has_unadmitted_nodes:
        overall_status = "approved" if complete_manual_review else "partial"
    elif incomplete_count > 0 and rejected_count == 0:
        overall_status = "partial"
    else:
        overall_status = "rejected"

    return {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "review_id": review_id,
        "proposal_id": proposal.get("proposal_id", ""),
        "proposal_hash": proposal_hash,
        "evidence_hash": evidence_hash,
        "reviewed_at": reviewed_at,
        "reviewer": reviewer,
        "edge_decisions": edge_decisions,
        "node_decisions": node_decisions,
        "overall_status": overall_status,
        "review_kind": "explicit_decisions" if complete_manual_review else "deterministic_screening",
        "interventions": (decisions or {}).get("interventions", []),
    }


def _finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError):
        return False


def _validate_sidecar(data: Any, name: str) -> None:
    """Validate the exact vocabulary used by the two local causal schemas.

    This bounded stdlib validator is deliberately limited to these contracts;
    unsupported schema keywords fail closed rather than being silently ignored.
    """
    schema_path = Path(__file__).resolve().parents[2] / "schemas" / f"{name}.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    supported = {"$id", "$schema", "title", "description", "type", "const", "enum", "format",
                 "pattern", "minimum", "maximum", "required", "properties", "additionalProperties", "items"}

    def refuse(pointer: str, message: str) -> None:
        raise ValueError(f"SIDECAR_SCHEMA: {name}{pointer}: {message}")

    def walk(value: Any, rule: dict[str, Any], pointer: str = "", depth: int = 0) -> None:
        if depth > 64 or set(rule) - supported:
            refuse(pointer, "unsupported contract or excessive nesting")
        types = rule.get("type")
        if types:
            types = types if isinstance(types, list) else [types]
            matches = {"object": isinstance(value, dict), "array": isinstance(value, list),
                       "string": isinstance(value, str), "number": _finite_number(value),
                       "integer": type(value) is int, "boolean": type(value) is bool, "null": value is None}
            if not any(matches.get(kind, False) for kind in types):
                refuse(pointer, f"expected {types}")
        if "const" in rule and value != rule["const"]:
            refuse(pointer, "constant differs")
        if "enum" in rule and not any(value == item and (type(value) is type(item) or _finite_number(value) and _finite_number(item)) for item in rule["enum"]):
            refuse(pointer, "invalid enum")
        if isinstance(value, str):
            if "pattern" in rule and re.search(rule["pattern"], value) is None:
                refuse(pointer, "pattern differs")
            if rule.get("format") == "date-time":
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value):
                    refuse(pointer, "date-time requires an ISO timestamp and timezone")
                try:
                    datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    refuse(pointer, "invalid calendar timestamp")
        if _finite_number(value):
            if "minimum" in rule and value < rule["minimum"] or "maximum" in rule and value > rule["maximum"]:
                refuse(pointer, "number out of bounds")
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                refuse(pointer, "object keys must be strings")
            if set(rule.get("required", [])) - set(value):
                refuse(pointer, "required fields missing")
            properties = rule.get("properties", {})
            additional = rule.get("additionalProperties", True)
            for key, child in value.items():
                child_pointer = pointer + "/" + key.replace("~", "~0").replace("/", "~1")
                child_rule = properties.get(key)
                if child_rule is None:
                    if additional is False:
                        refuse(child_pointer, "unknown field")
                    child_rule = additional if isinstance(additional, dict) else None
                if child_rule is not None:
                    walk(child, child_rule, child_pointer, depth + 1)
        elif isinstance(value, list) and "items" in rule:
            for index, child in enumerate(value):
                walk(child, rule["items"], f"{pointer}/{index}", depth + 1)

    walk(data, schema)


def _verify_staged_execution(stage: Path) -> str:
    """Run the public disk-based pipeline before publishing any workspace."""
    from compile_model import compile_workspace

    from .validator import validate_edges, validate_nodes

    nodes, node_issues = load_json_secure(stage / "nodes.json")
    edges, edge_issues = load_json_secure(stage / "edges.json")
    manifest, manifest_issues = load_json_secure(stage / "simulation-manifest.json")
    if (node_issues or edge_issues or manifest_issues
            or not isinstance(nodes, list) or not all(isinstance(node, dict) for node in nodes)
            or not isinstance(edges, list) or not isinstance(manifest, dict)):
        raise ValueError("INVALID_STAGED_ARTIFACT")
    evidence_ids = {eid for node in nodes for eid in node.get("evidence_ids", [])}
    node_check, node_ids = validate_nodes(nodes, evidence_ids, manifest)
    edge_check, _ = validate_edges(edges, node_ids, evidence_ids,
        {n["id"]: n["type"] for n in nodes}, {n["id"]: n["scale"] for n in nodes})
    if node_check.errors or edge_check.errors:
        raise ValueError(f"STAGED_SCHEMA_FAILED: {[i.to_dict() for i in node_check.errors + edge_check.errors]}")

    expected = compile_workspace(stage)
    write_json_atomic(stage / "simulation-model.json", expected)
    scripts = Path(__file__).resolve().parents[1]
    for script in ("run_simulation.py", "replay_simulation.py"):
        completed = subprocess.run(
            [sys.executable, str(scripts / script), "--workspace", str(stage), "--json"],
            capture_output=True, text=True, timeout=60,
        )
        if completed.returncode:
            raise ValueError(f"STAGED_EXECUTION_FAILED: {script}: {completed.stdout[-2000:]} {completed.stderr[-1000:]}")
    saved, problems = load_json_secure(stage / "simulation-model.json")
    replay, replay_problems = load_json_secure(stage / "replay-report.json")
    if problems or replay_problems or saved != expected or not isinstance(replay, dict) or replay.get("match") is not True:
        raise ValueError("STAGED_MODEL_MISMATCH: compile, disk execution and replay must agree")
    result_hash = expected.get("model_hash")
    if not isinstance(result_hash, str):
        raise ValueError("STAGED_MODEL_HASH_MISSING")
    return result_hash


def _publish_workspace(stage: Path, target: Path) -> None:
    """Publish once; restore an empty Windows target if rename fails."""
    removed_empty = False
    if sys.platform == "win32" and target.exists():
        target.rmdir()  # Fails safely if another writer populated the directory.
        removed_empty = True
    try:
        os.replace(stage, target)
    except OSError:
        if removed_empty and not os.path.lexists(target):
            target.mkdir()
        raise


def _merge_items_by_id(existing: Any, incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    base = [dict(item) for item in existing if isinstance(item, dict)] if isinstance(existing, list) else []
    by_id: dict[str, int] = {}
    for idx, item in enumerate(base):
        item_id = item.get("id")
        if isinstance(item_id, str) and item_id:
            by_id[item_id] = idx
    for item in incoming:
        item_id = item.get("id")
        if isinstance(item_id, str) and item_id in by_id:
            base[by_id[item_id]] = dict(item)
        else:
            if isinstance(item_id, str) and item_id:
                by_id[item_id] = len(base)
            base.append(dict(item))
    return base


def apply_model(
    proposal: dict[str, Any],
    review: dict[str, Any],
    target_dir: Path | str,
    *,
    workspace_id: str = "workspace:auto-causal-model",
    into_initialized: bool = False,
) -> dict[str, Any]:
    """Export a draft or publish a reviewed, replayed model to a fresh or initialized workspace."""
    _validate_sidecar(proposal, "causal-proposal")
    _validate_sidecar(review, "causal-review")
    expected_hash = f"sha256:{canonical_hash(proposal)}"
    if review.get("proposal_hash") != expected_hash:
        raise ValueError("Review proposal_hash mismatch")
    if review.get("evidence_hash") != proposal.get("evidence_hash") or review.get("proposal_id") != proposal.get("proposal_id"):
        raise ValueError("Review evidence_hash or proposal_id mismatch")
    target = Path(os.path.abspath(target_dir))
    if path_contains_link_or_reparse(target):
        raise ValueError("REFUSED_LINKED_WORKSPACE")
    if into_initialized:
        manifest_path = target / "simulation-manifest.json"
        if not target.is_dir() or not manifest_path.is_file():
            return {
                "ok": False,
                "error": "MISSING_INITIALIZED_MANIFEST: --into-initialized requires an existing workspace with simulation-manifest.json",
                "workspace_dir": str(target),
                "compilation_ok": False,
                "simulation_run_ok": False,
            }
        existing_manifest_doc, existing_m_issues = load_json_secure(manifest_path)
        raw_artifact_paths = (
            existing_manifest_doc.get("artifact_paths")
            if not existing_m_issues and isinstance(existing_manifest_doc, dict)
            else None
        )
        existing_paths_doc: dict[str, Any] = (
            raw_artifact_paths if isinstance(raw_artifact_paths, dict) else {}
        )
        protected_files = {
            "execution-trace.json",
            "simulation-model.json",
            "simulation-run.json",
            "replay-report.json",
        }
        for key in ("execution_trace", "computational_model", "run_ledger", "replay_report"):
            rel_val = existing_paths_doc.get(key)
            if isinstance(rel_val, str) and rel_val.strip():
                protected_files.add(rel_val.strip().replace("\\", "/"))
        for fname in sorted(protected_files):
            candidate = target / fname
            if candidate.exists():
                raise ValueError(
                    f"REFUSED_EXISTING_SIMULATION_DATA: {fname} already exists in initialized workspace"
                )
        prop_candidates = {"propagation-trace.jsonl"}
        raw_prop_rel = existing_paths_doc.get("propagation_trace")
        if isinstance(raw_prop_rel, str) and raw_prop_rel.strip():
            prop_candidates.add(raw_prop_rel.strip().replace("\\", "/"))
        for prop_rel in sorted(prop_candidates):
            prop_path = target / prop_rel
            if not prop_path.exists():
                continue
            if not prop_path.is_file():
                raise ValueError(
                    f"REFUSED_EXISTING_SIMULATION_DATA: {prop_rel} already exists in initialized workspace"
                )
            if prop_path.stat().st_size > 0:
                stub_rows, stub_issues = load_jsonl_secure(prop_path)
                is_template_stub = (
                    not stub_issues
                    and len(stub_rows) == 1
                    and isinstance(stub_rows[0], dict)
                    and stub_rows[0].get("edge_id") == "causal:example-increases-target"
                )
                if not is_template_stub:
                    raise ValueError(
                        f"REFUSED_EXISTING_SIMULATION_DATA: non-template {prop_rel} already exists in initialized workspace"
                    )
    elif target.exists() and (not target.is_dir() or any(target.iterdir())):
        return {"ok": False, "error": "REFUSED_EXISTING_WORKSPACE: target must be new or empty",
                "workspace_dir": str(target), "compilation_ok": False, "simulation_run_ok": False}
    if review.get("overall_status") == "rejected":
        return {"ok": False, "error": "REJECTED_MODEL", "workspace_dir": str(target),
                "compilation_ok": False, "simulation_run_ok": False, "overall_status": "rejected"}
    if review.get("overall_status") not in {"approved", "partial"}:
        raise ValueError("INVALID_REVIEW_STATUS")

    node_decisions = review.get("node_decisions", {})
    edge_decisions = review.get("edge_decisions", {})
    admitted_edges = []
    for candidate in proposal.get("candidate_edges", []):
        decision = edge_decisions.get(candidate["id"], {})
        if decision.get("decision") not in {"admit", "revise"}:
            continue
        edge = {**candidate, **decision.get("modifications", {})}
        if any(node_decisions.get(edge[endpoint], {}).get("decision") not in {"admit", "revise"} for endpoint in ("from", "to")):
            continue
        if (isinstance(edge.get("sign"), bool) or edge.get("sign") not in (-1, 1)
                or not _finite_number(edge.get("effect_size"))
                or type(edge.get("lag_ticks")) is not int or edge["lag_ticks"] < 0
                or not isinstance(edge.get("mechanism"), str) or not edge["mechanism"].strip()):
            continue
        ticks = edge["lag_ticks"]
        compiled = {
            "id": edge["id"], "from": edge["from"], "to": edge["to"],
            "relation": edge.get("relation", "causes"), "sign": edge["sign"],
            "base_strength": abs(edge["effect_size"]),
            "evidence_confidence": edge.get("evidence_confidence"),
            "confidence": edge.get("evidence_confidence"), "mechanism": edge["mechanism"],
            "lag_ticks": ticks, "lag_unit": edge.get("lag_unit", "ticks"),
            "lag_distribution": edge.get("lag_distribution", {"type": "fixed", "fixed": f"P{ticks}D"}),
            "context_modifiers": edge.get("context_modifiers", []), "status": "admitted",
            "transform": edge.get("transform", "linear"),
            "effect_parameter": edge.get("effect_parameter", {"kind": "reference_value", "reference_value": abs(edge["effect_size"])}),
            "evidence": edge.get("evidence_ids", []),
        }
        for key in ("transform_parameters", "weight_distribution", "integration", "saturation", "existence_prob", "gate"):
            if key in edge:
                compiled[key] = edge[key]
        admitted_edges.append(compiled)
    ids = {e[k] for e in admitted_edges for k in ("from", "to")}
    admitted_nodes = []
    for candidate in proposal.get("candidate_nodes", []):
        if candidate["id"] not in ids:
            continue
        decision = node_decisions.get(candidate["id"], {})
        node = {**candidate, **decision.get("modifications", {})}
        compiled = {
            "id": node["id"], "type": "factor", "name": node["name"],
            "description": node.get("description", node.get("source_anchor", "")),
            "time": node.get("time"), "status": node.get("status"),
            "timeline": node.get("timeline", "observed_baseline"),
            "state_before": {"summary": "Reviewed baseline", "value": node.get("baseline")},
            "trigger": {"kind": "endogenous_propagation", "description": "Reviewed transmission path"},
            "mechanism": node.get("mechanism", node.get("source_anchor", "")),
            "state_after": {"summary": "Baseline before simulation", "value": node.get("baseline")},
            "lag": "P0D", "scale": node.get("scale", "level"), "unit": node.get("unit", ""),
            "baseline": node.get("baseline"), "confidence": node.get("confidence"),
            "evidence_ids": node.get("evidence_ids", []), "role": node.get("role", "endogenous"),
        }
        if node.get("scale") == "stock":
            compiled["retention"] = node.get("retention")
        for key in ("bounds", "decay_rate", "retention_distribution", "decay_rate_distribution", "initial_value"):
            if key in node:
                compiled[key] = node[key]
        admitted_nodes.append(compiled)
    if not admitted_edges or len(admitted_nodes) != len(ids):
        if into_initialized and review.get("overall_status") == "partial":
            draft_edges = [
                dict(c)
                for c in proposal.get("candidate_edges", [])
                if edge_decisions.get(c["id"], {}).get("decision") in {"admit", "revise", "incomplete"}
            ]
            draft_ids = {e[k] for e in draft_edges for k in ("from", "to")}
            draft_nodes = [
                dict(c)
                for c in proposal.get("candidate_nodes", [])
                if c["id"] in draft_ids
            ]
            if not draft_edges or len(draft_nodes) != len(draft_ids):
                return {"ok": False, "error": "NO_COMPLETE_CAUSAL_GRAPH", "compilation_ok": False, "simulation_run_ok": False}
            admitted_edges_for_draft = draft_edges
            admitted_nodes_for_draft = draft_nodes
        else:
            return {"ok": False, "error": "NO_COMPLETE_CAUSAL_GRAPH", "compilation_ok": False, "simulation_run_ok": False}
    else:
        admitted_edges_for_draft = admitted_edges
        admitted_nodes_for_draft = admitted_nodes
    approved = review["overall_status"] == "approved"
    runnable = approved and bool(admitted_edges) and not proposal.get("gaps") and all(
        _finite_number(n.get("baseline")) and n.get("time") and n.get("unit")
        and n.get("status") != "assumption" and len(n["mechanism"].split()) >= 10
        and (n["scale"] != "stock" or _finite_number(n.get("retention"))) for n in admitted_nodes
    ) and all(len(e["mechanism"].split()) >= 10 for e in admitted_edges)
    if approved and (not runnable or not review.get("reviewer", "").startswith(("human:", "agent:")) or review.get("review_kind") != "explicit_decisions"):
        raise ValueError("INCOMPLETE_APPROVAL: explicit review and grounded node/edge parameters are required")
    manifest = {
        "schema_version": "2.1.0", "formula_version": FORMULA_VERSION, "simulation_id": workspace_id,
        "status": "initialized" if runnable else "draft", "created_at": review["reviewed_at"],
        "simulation_mode": "deterministic", "assurance_tier": "limited",
        "artifact_paths": {"nodes": "nodes.json", "edges": "edges.json",
            "causal_proposal": "causal-proposal.json", "causal_review": "causal-review.json",
            "assumptions": "assumptions.json", "computational_model": "simulation-model.json",
            "run_ledger": "simulation-run.json", "execution_trace": "execution-trace.json",
            "replay_report": "replay-report.json"},
    }
    node_filename = "nodes.json" if runnable else "candidate-nodes.json"
    edge_filename = "edges.json" if runnable else "candidate-edges.json"
    if not runnable:
        manifest["artifact_paths"] = {
            "candidate_nodes": node_filename, "candidate_edges": edge_filename,
            "causal_proposal": "causal-proposal.json", "causal_review": "causal-review.json",
            "assumptions": "assumptions.json",
        }
    if into_initialized:
        existing_manifest, m_issues = load_json_secure(target / "simulation-manifest.json")
        if m_issues or not isinstance(existing_manifest, dict):
            raise ValueError("INVALID_INITIALIZED_MANIFEST")
        merged_manifest = dict(existing_manifest)
        existing_paths = dict(merged_manifest.get("artifact_paths") or {})
        target_node_rel = str(existing_paths.get("nodes" if runnable else "candidate_nodes") or node_filename)
        target_edge_rel = str(existing_paths.get("edges" if runnable else "candidate_edges") or edge_filename)
        existing_nodes, _ = (load_json_secure(target / target_node_rel) if (target / target_node_rel).is_file() else ([], []))
        existing_edges, _ = (load_json_secure(target / target_edge_rel) if (target / target_edge_rel).is_file() else ([], []))
        incoming_nodes = admitted_nodes if runnable else admitted_nodes_for_draft
        incoming_edges = admitted_edges if runnable else admitted_edges_for_draft
        merged_nodes = _merge_items_by_id(existing_nodes, incoming_nodes)
        merged_edges = _merge_items_by_id(existing_edges, incoming_edges)

        existing_paths["causal_proposal"] = "causal-proposal.json"
        existing_paths["causal_review"] = "causal-review.json"
        existing_paths["assumptions"] = "assumptions.json"
        if runnable:
            if merged_manifest.get("simulation_mode") in (None, "qualitative"):
                merged_manifest["simulation_mode"] = "deterministic"
            existing_paths.pop("propagation_trace", None)
            existing_paths.setdefault("nodes", target_node_rel)
            existing_paths.setdefault("edges", target_edge_rel)
            existing_paths.setdefault("interventions", "interventions.json")
            existing_paths.setdefault("simulation_config", "simulation-config.json")
            existing_paths.setdefault("computational_model", "simulation-model.json")
            existing_paths.setdefault("run_ledger", "simulation-run.json")
            existing_paths.setdefault("execution_trace", "execution-trace.json")
            existing_paths.setdefault("replay_report", "replay-report.json")
        else:
            existing_paths["candidate_nodes"] = target_node_rel
            existing_paths["candidate_edges"] = target_edge_rel
        merged_manifest["artifact_paths"] = existing_paths

        assumptions_doc = {
            "schema_version": "1.0.0",
            "gaps": proposal.get("gaps", []),
            "assumptions": [n for n in proposal.get("candidate_nodes", []) if n.get("status") == "assumption"],
        }
        verified_hash = None
        if runnable:
            interventions = review.get("interventions", [])
            if not isinstance(interventions, list):
                raise ValueError("INVALID_REVIEW_INTERVENTIONS")
            staged = Path(tempfile.mkdtemp(prefix=".aleph-merge-", dir=target.parent))
            try:
                write_json_atomic(staged / "nodes.json", merged_nodes)
                write_json_atomic(staged / "edges.json", merged_edges)
                write_json_atomic(staged / "interventions.json", interventions)
                staged_config: dict[str, Any] = {"mode": "deterministic", "seed": "42", "ticks": 5, "timestep": 1.0}
                existing_cfg_path = target / "simulation-config.json"
                if existing_cfg_path.is_file():
                    loaded_cfg, cfg_issues = load_json_secure(existing_cfg_path)
                    if not cfg_issues and isinstance(loaded_cfg, dict):
                        staged_config = loaded_cfg
                else:
                    engine_config = merged_manifest.get("engine_config")
                    temporal_frame = merged_manifest.get("temporal_frame")
                    ts_raw = engine_config.get("timestep") if isinstance(engine_config, dict) else None
                    if ts_raw is None and isinstance(temporal_frame, dict):
                        ts_raw = temporal_frame.get("timestep")
                    ts_sec = parse_timestep_seconds(ts_raw) if ts_raw is not None else None
                    if ts_sec is not None and ts_sec > 0:
                        staged_config["timestep"] = ts_sec / 86400.0
                    if merged_manifest.get("simulation_mode") == "monte_carlo":
                        staged_config["mode"] = "monte_carlo"
                write_json_atomic(staged / "simulation-config.json", staged_config)
                write_json_atomic(staged / "simulation-manifest.json", merged_manifest)
                branch_rel = str(existing_paths.get("branch_ledger", "branch-ledger.json"))
                target_branch_path = target / branch_rel
                if target_branch_path.is_file():
                    shutil.copyfile(target_branch_path, staged / branch_rel)
                verified_hash = _verify_staged_execution(staged)
                for fname in (
                    "interventions.json",
                    "simulation-config.json",
                    "simulation-model.json",
                    "simulation-run.json",
                    "execution-trace.json",
                    "replay-report.json",
                ):
                    if (staged / fname).is_file():
                        shutil.copyfile(staged / fname, target / fname)
                if (staged / branch_rel).is_file() and target_branch_path.is_file():
                    shutil.copyfile(staged / branch_rel, target_branch_path)
                stub_trace = target / "propagation-trace.jsonl"
                if stub_trace.is_file():
                    stub_rows, _ = load_jsonl_secure(stub_trace)
                    if (
                        len(stub_rows) == 1
                        and isinstance(stub_rows[0], dict)
                        and stub_rows[0].get("edge_id") == "causal:example-increases-target"
                    ):
                        stub_trace.unlink(missing_ok=True)
            finally:
                if staged.exists():
                    shutil.rmtree(staged)
        write_json_atomic(target / target_node_rel, merged_nodes)
        write_json_atomic(target / target_edge_rel, merged_edges)
        write_json_atomic(target / "causal-proposal.json", proposal)
        write_json_atomic(target / "causal-review.json", review)
        write_json_atomic(target / "assumptions.json", assumptions_doc)
        write_json_atomic(target / "simulation-manifest.json", merged_manifest)
        return {
            "ok": True,
            "workspace_dir": str(target),
            "into_initialized": True,
            "runnable": bool(runnable),
            "status": merged_manifest.get("status", "initialized" if runnable else "draft"),
            "admitted_edge_count": len(admitted_edges),
            "admitted_node_count": len(admitted_nodes),
            "compilation_ok": bool(runnable),
            "simulation_run_ok": bool(runnable),
            "replay_ok": bool(runnable),
            "model_hash": verified_hash,
            "gaps_count": len(proposal.get("gaps", [])),
            "overall_status": review["overall_status"],
        }

    target.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=".aleph-apply-", dir=target.parent))
    verified_hash = None
    try:
        for name, value in {
            node_filename: admitted_nodes, edge_filename: admitted_edges,
            "causal-proposal.json": proposal, "causal-review.json": review,
            "simulation-manifest.json": manifest,
            "assumptions.json": {"schema_version": "1.0.0", "gaps": proposal.get("gaps", []),
                "assumptions": [n for n in proposal.get("candidate_nodes", []) if n.get("status") == "assumption"]},
        }.items():
            write_json_atomic(staged / name, value)
        if runnable:
            interventions = review.get("interventions", [])
            if not isinstance(interventions, list):
                raise ValueError("INVALID_REVIEW_INTERVENTIONS")
            write_json_atomic(staged / "interventions.json", interventions)
            write_json_atomic(staged / "simulation-config.json", {"mode": "deterministic", "seed": "42", "ticks": 5, "timestep": 1.0})
            verified_hash = _verify_staged_execution(staged)
        # One rename publishes the entire directory on the same filesystem.
        # Refuse a target populated by another writer during staging.
        if path_contains_link_or_reparse(target) or (target.exists() and (not target.is_dir() or any(target.iterdir()))):
            raise ValueError("WORKSPACE_CHANGED_DURING_STAGING")
        _publish_workspace(staged, target)
    finally:
        if staged.exists():
            shutil.rmtree(staged)
    return {"ok": True, "workspace_dir": str(target), "runnable": bool(runnable),
        "status": "ready" if runnable else "draft", "admitted_edge_count": len(admitted_edges),
        "admitted_node_count": len(admitted_nodes), "compilation_ok": bool(runnable),
        "simulation_run_ok": bool(runnable), "replay_ok": bool(runnable), "model_hash": verified_hash,
        "gaps_count": len(proposal.get("gaps", [])), "overall_status": review["overall_status"]}
