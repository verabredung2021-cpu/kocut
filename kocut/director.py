"""문장 단위 rough-cut 플래너.

v0.9의 목표는 "더 많이 자르기"가 아니라 편집자가 납득할 수 있는 판단을
만드는 것입니다. 자동 컷은 확실한 것만 적용하고, 나머지는 CSV/HTML에서 검토합니다.
"""
from __future__ import annotations

import csv
import html
import math
import re
from pathlib import Path
from typing import Iterable

from rapidfuzz import fuzz

from kocut.quality import QualityPreset
from kocut.types import CutCandidate, CutKind, TopicSection, Utterance, Word

_ENDING_RE = re.compile(
    r"(다|요|죠|네요|군요|거든요|습니다|습니까|까요|예요|이에요|돼요|합니다|됩니다|했어요|했죠)$"
)
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.,!?%])")
_MULTISPACE = re.compile(r"\s+")
_RETAKE_MARKERS = (
    "다시 갈게요", "다시 갈께요", "다시 할게요", "다시 한번", "다시 한 번",
    "한번 더 갈게요", "한 번 더 갈게요", "처음부터", "다시 찍", "다시 녹화",
    "컷", "엔지", "NG", "스톱", "스탑", "끊을게요",
)
_STUMBLE_MARKERS = (
    "아니", "아 아니다", "아닌데", "그게 아니라", "잠깐만", "잠시만", "죄송",
    "틀렸", "잘못", "다시 말", "뭐였지", "어디까지", "말이 안", "헷갈",
)
_GENERIC_LOW_INFO = {"네", "네네", "자", "좋습니다", "좋아요", "알겠습니다", "오케이", "오케이요"}
_FILLER_WORDS = {"어", "음", "아", "에", "흠", "이제"}
_PROTECTED_CONNECTORS = {"근데", "그래서", "이제", "그리고", "그런데", "그래도", "그러니까", "그러면", "그럼", "또는"}
_PRODUCTION_MARKERS = (
    "촬영 준비", "구도", "인사부터 다시", "다시 하세요", "다시 해주세요",
    "조금만 보고 할게요", "보고 할게요", "잠깐만요", "잠깐만", "잠시만",
    "끝났습니다", "잘 편집", "편집해 주시고", "카메라", "마이크", "오디오",
    "녹음", "대기", "컷", "커트", "스탑", "스톱",
)
_SOFT_PRODUCTION_MARKERS = (
    "안녕하세요", "원장님 이번에", "네 감사합니다", "감사합니다", "맞아요", "네.", "네",
)
_TOPIC_KEYWORDS = (
    "AMH", "FSH", "NK세포", "NK", "자궁경", "자궁근종", "조기 폐경", "조기폐경",
    "난소 저반응", "난소저반응", "난소", "난포", "공난포", "난자", "배아", "착상",
    "내막", "에스트로겐", "에스로겐", "프로게스테론", "프로게스토리", "면역글로블린",
    "난자활성화", "난자 활성화", "미세수정", "미세 수정", "방추사", "방수사",
    "3일 배아", "5일 배아", "오일 배아", "시험관", "이식", "채취", "임신",
    "결론", "핵심", "중요", "문제", "이유", "방법", "수술", "치료", "검사", "증상",
    "원인", "위험", "관리", "진단", "호르몬", "생리", "시술", "상담",
)
_QUESTION_HINTS = ("있나요", "인가요", "까요", "어떻게 생각", "이유가", "말씀이", "들었는데", "하나요", "했는데요")
_GENERIC_LOW_INFO_NORMALISED: set[str] = set()


def _clean_word(text: str) -> str:
    return text.strip().strip('"\'“”‘’')


def _join_words(words: Iterable[Word]) -> str:
    text = " ".join(_clean_word(w.word) for w in words if _clean_word(w.word))
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _MULTISPACE.sub(" ", text)
    return text.strip()


def _normalise_for_match(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", text).lower()


def _low_info_norms() -> set[str]:
    global _GENERIC_LOW_INFO_NORMALISED
    if not _GENERIC_LOW_INFO_NORMALISED:
        _GENERIC_LOW_INFO_NORMALISED = {_normalise_for_match(x) for x in _GENERIC_LOW_INFO}
    return _GENERIC_LOW_INFO_NORMALISED


def _looks_like_sentence_end(text: str) -> bool:
    t = _clean_word(text).rstrip("…")
    if not t:
        return False
    if t.endswith((".", "?", "!")):
        return True
    return bool(_ENDING_RE.search(t))


def _word_count(text: str) -> int:
    return len([p for p in re.split(r"\s+", text.strip()) if p])


def _fmt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    if h:
        return f"{h}:{m:02d}:{s:04.1f}"
    return f"{m}:{s:04.1f}"


def _looks_like_production_text(text: str) -> bool:
    compact = re.sub(r"\s+", " ", text.strip())
    norm = _normalise_for_match(compact)
    return any(_normalise_for_match(marker) in norm for marker in _PRODUCTION_MARKERS)


def _looks_like_soft_production_text(text: str) -> bool:
    compact = re.sub(r"\s+", " ", text.strip())
    if len(compact) > 34:
        return False
    norm = _normalise_for_match(compact)
    return any(_normalise_for_match(marker) in norm for marker in _SOFT_PRODUCTION_MARKERS)


def _looks_like_question(text: str) -> bool:
    t = text.strip()
    return "?" in t or any(h in t for h in _QUESTION_HINTS)


def build_utterances(
    words: list[Word],
    *,
    max_duration: float = 9.0,
    max_words: int = 24,
    max_chars: int = 110,
    min_duration: float = 1.0,
    boundary_gap: float = 0.82,
) -> list[Utterance]:
    """단어 타임스탬프를 문장/호흡 단위로 묶습니다."""
    valid = sorted((w for w in words if w.end > w.start and _clean_word(w.word)), key=lambda w: (w.start, w.end))
    if not valid:
        return []
    result: list[Utterance] = []
    cur: list[Word] = []

    def flush() -> None:
        if not cur:
            return
        text = _join_words(cur)
        if not text:
            cur.clear()
            return
        result.append(
            Utterance(
                index=len(result) + 1,
                start=cur[0].start,
                end=cur[-1].end,
                text=text,
                word_count=_word_count(text),
                confidence=min(1.0, max(0.55, sum((w.prob or 0.75) for w in cur) / max(1, len(cur)))),
            )
        )
        cur.clear()

    for i, word in enumerate(valid):
        cur.append(word)
        text = _join_words(cur)
        dur = cur[-1].end - cur[0].start
        next_gap = (valid[i + 1].start - word.end) if i + 1 < len(valid) else 999.0
        next_text = _clean_word(valid[i + 1].word) if i + 1 < len(valid) else ""
        force = dur >= max_duration or len(cur) >= max_words or len(text) >= max_chars
        natural = (
            dur >= min_duration
            and (_looks_like_sentence_end(word.word) or next_gap >= boundary_gap)
            and next_text not in {"수", "것", "거", "데", "때문", "정도"}
        )
        if force or natural or i + 1 == len(valid):
            flush()
    return result


def _gap_quantile(gaps: list[float], q: float) -> float:
    if not gaps:
        return 0.0
    ordered = sorted(gaps)
    if len(ordered) == 1:
        return ordered[0]
    pos = max(0.0, min(1.0, q)) * (len(ordered) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def sentence_boundary_silence_cuts(
    utterances: list[Utterance],
    total_duration: float,
    *,
    preset: QualityPreset,
    min_silence_ms: int | None = None,
    pad_before_ms: int | None = None,
    pad_after_ms: int | None = None,
    min_cut_ms: int | None = None,
) -> list[CutCandidate]:
    """문장/호흡 단위 사이의 gap만 삭제 후보로 삼습니다."""
    total_duration = total_duration if math.isfinite(total_duration) and total_duration > 0 else 0.0
    units = [u for u in sorted(utterances, key=lambda x: (x.start, x.end)) if u.end > u.start]
    if len(units) < 2:
        return []
    min_gap = max(0.0, (preset.min_silence_ms if min_silence_ms is None else min_silence_ms) / 1000.0)
    pad_before = max(0.0, (preset.pad_before_ms if pad_before_ms is None else pad_before_ms) / 1000.0)
    pad_after = max(0.0, (preset.pad_after_ms if pad_after_ms is None else pad_after_ms) / 1000.0)
    min_cut = max(0.0, (preset.min_cut_ms if min_cut_ms is None else min_cut_ms) / 1000.0)

    pairs: list[tuple[Utterance, Utterance, float]] = []
    gaps: list[float] = []
    for left, right in zip(units, units[1:]):
        gap = right.start - left.end
        if gap <= 0:
            continue
        pairs.append((left, right, gap))
        if 0.15 <= gap <= 20.0:
            gaps.append(gap)
    q_by_profile = {"safe": 0.78, "balanced": 0.66, "cutback": 0.55, "aggressive": 0.44}
    adaptive = _gap_quantile(gaps, q_by_profile.get(preset.name, 0.66)) if len(gaps) >= 6 else 0.0
    threshold = max(min_gap, adaptive)

    cuts: list[CutCandidate] = []
    for left, right, gap in pairs:
        if gap < threshold:
            continue
        breath = max(0.0, min(0.22, (1.9 - gap) * 0.11))
        start = left.end + pad_after + breath
        end = right.start - pad_before - breath
        if total_duration:
            start = min(max(0.0, start), total_duration)
            end = min(max(0.0, end), total_duration)
        dur = end - start
        if dur < min_cut:
            continue
        boundary_bonus = 0.08 if _looks_like_sentence_end(left.text) else 0.0
        long_bonus = min(0.20, max(0.0, gap - threshold) / 6.0)
        cuts.append(
            CutCandidate(
                start=start,
                end=end,
                kind=CutKind.SILENCE,
                reason=f"문장 경계 무음 gap {gap:.2f}초 → {dur:.2f}초 삭제 · {preset.name}",
                text=f"{left.text[-24:]} … {right.text[:24]}",
                confidence=min(0.98, 0.78 + boundary_bonus + long_bonus),
            )
        )
    return cuts


def detect_production_cuts(utterances: list[Utterance]) -> list[CutCandidate]:
    """촬영 지시/제작 현장 멘트를 자동 제거 후보로 검출합니다."""
    units = sorted(utterances, key=lambda u: (u.start, u.end))
    hits: set[int] = set()
    for i, u in enumerate(units):
        if not _looks_like_production_text(u.text):
            continue
        hits.add(i)
        j = i - 1
        while j >= 0 and units[j + 1].start - units[j].end <= 2.6 and _looks_like_soft_production_text(units[j].text):
            hits.add(j)
            j -= 1
        j = i + 1
        while j < len(units) and units[j].start - units[j - 1].end <= 2.6 and _looks_like_soft_production_text(units[j].text):
            hits.add(j)
            j += 1

    if not hits:
        return []
    ranges: list[tuple[int, int]] = []
    ordered = sorted(hits)
    start = prev = ordered[0]
    for idx in ordered[1:]:
        if idx == prev + 1 or units[idx].start - units[prev].end <= 2.6:
            prev = idx
            continue
        ranges.append((start, prev))
        start = prev = idx
    ranges.append((start, prev))

    cuts: list[CutCandidate] = []
    for a, b in ranges:
        text = " ".join(u.text for u in units[a : b + 1]).strip()
        cuts.append(
            CutCandidate(
                start=units[a].start,
                end=units[b].end,
                kind=CutKind.PRODUCTION,
                reason="제작 현장 멘트/재시작 안내 자동 제거",
                text=text,
                confidence=0.96,
            )
        )
    return cuts


# v0.9 개발 중 이름 호환
detect_production_chatter = detect_production_cuts


def detect_review_candidates(utterances: list[Utterance], words: list[Word] | None = None) -> list[CutCandidate]:
    """자동 컷하지 않고 사람이 확인해야 할 후보를 검출합니다."""
    production_cuts = detect_production_cuts(utterances)
    candidates: list[CutCandidate] = []
    units = sorted(utterances, key=lambda u: (u.start, u.end))
    for u in units:
        text = u.text.strip()
        norm = _normalise_for_match(text)
        lowered = text.lower()
        if any(pc.start < u.end and pc.end > u.start for pc in production_cuts):
            continue
        for marker in _RETAKE_MARKERS:
            if marker.lower() in lowered:
                candidates.append(CutCandidate(start=u.start, end=u.end, kind=CutKind.RETAKE, reason=f"NG/재촬영 마커 후보: {marker}", text=text, confidence=0.78))
                break
        else:
            stumble_hit = any(m in text for m in _STUMBLE_MARKERS)
            if "아니지만" in text or "아니잖" in text:
                stumble_hit = False
            if stumble_hit:
                candidates.append(CutCandidate(start=u.start, end=u.end, kind=CutKind.RETAKE, reason="말실수/정정 표현 후보", text=text, confidence=0.58))
            elif norm in _low_info_norms() and u.duration <= 2.2:
                candidates.append(CutCandidate(start=u.start, end=u.end, kind=CutKind.LOW_INFO, reason="저정보 연결 발화 후보", text=text, confidence=0.52))
    for i in range(len(units) - 1):
        a = _normalise_for_match(units[i].text)
        b = _normalise_for_match(units[i + 1].text)
        if len(a) < 8 or len(b) < 8:
            continue
        sim = fuzz.ratio(a, b)
        if sim >= 76:
            candidates.append(CutCandidate(start=units[i].start, end=units[i].end, kind=CutKind.RETAKE, reason=f"반복 문장 후보 — 다음 문장과 유사도 {sim:.0f}%", text=units[i].text, confidence=min(0.92, sim / 100.0)))
    if words:
        for u in units:
            if any(pc.start < u.end and pc.end > u.start for pc in production_cuts):
                continue
            ws = [w for w in words if w.start >= u.start - 1e-6 and w.end <= u.end + 1e-6]
            if not ws:
                continue
            cleaned = [_clean_word(w.word).rstrip("?!.,…") for w in ws]
            density_words = [w for w in cleaned if w in _FILLER_WORDS and w != "이제"]
            connector_count = sum(w in _PROTECTED_CONNECTORS for w in cleaned)
            filler_count = len(density_words)
            ratio = filler_count / max(1, len(ws))
            if filler_count >= 2 and ratio >= 0.34 and connector_count == 0 and u.duration <= 5.0:
                candidates.append(CutCandidate(start=u.start, end=u.end, kind=CutKind.FILLER, reason=f"간투사 밀집 후보 ({filler_count}/{len(ws)})", text=u.text, confidence=0.55))
    candidates.sort(key=lambda c: (c.start, c.end, -c.confidence))
    deduped: list[CutCandidate] = []
    for c in candidates:
        if deduped and abs(deduped[-1].start - c.start) < 0.05 and abs(deduped[-1].end - c.end) < 0.05:
            if c.confidence > deduped[-1].confidence:
                deduped[-1] = c
        else:
            deduped.append(c)
    return deduped


def _topic_title_for_text(text: str, fallback: str) -> tuple[str, list[str]]:
    hits: list[str] = []
    for kw in _TOPIC_KEYWORDS:
        if kw in text and kw not in hits:
            hits.append(kw)
    if any(k in hits for k in ("AMH", "FSH", "난소 저반응", "난소저반응", "조기 폐경", "조기폐경")):
        title = "난소기능·호르몬 수치"
    elif any(k in hits for k in ("NK", "NK세포", "착상", "자궁경", "내막")):
        title = "착상 준비·면역/자궁 환경"
    elif any(k in hits for k in ("배아", "3일 배아", "5일 배아", "오일 배아", "미세수정", "미세 수정", "난자활성화", "난자 활성화", "방추사", "방수사")):
        title = "배아 전략·보조생식술"
    elif any(k in hits for k in ("임신", "이식", "시험관", "시술")):
        title = "임신 성공 과정"
    else:
        title = fallback[:44] + ("…" if len(fallback) > 44 else "")
    question = next((part.strip() for part in re.split(r"(?<=[?])\s+", text) if _looks_like_question(part)), "")
    if question and hits:
        question = question[:48] + ("…" if len(question) > 48 else "")
        title = f"{title} — {question}"
    return title, hits[:10]


def build_topic_sections(utterances: list[Utterance], *, max_gap: float = 12.0, min_duration: float = 35.0) -> list[TopicSection]:
    """질문/답변 흐름과 난임 도메인 키워드를 기준으로 챕터 후보를 만듭니다."""
    units = sorted(utterances, key=lambda u: (u.start, u.end))
    if not units:
        return []
    groups: list[list[Utterance]] = []
    cur: list[Utterance] = []
    for i, u in enumerate(units):
        question_break = bool(cur) and _looks_like_question(u.text) and (u.start - cur[0].start) >= min_duration
        gap_break = bool(cur) and (u.start - cur[-1].end) >= max_gap and (cur[-1].end - cur[0].start) >= min_duration
        if question_break or gap_break:
            groups.append(cur)
            cur = []
        cur.append(u)
        if i == len(units) - 1 and cur:
            groups.append(cur)
    sections: list[TopicSection] = []
    for g in groups:
        text = " ".join(u.text for u in g)
        title, hits = _topic_title_for_text(text, g[0].text.strip())
        duration = g[-1].end - g[0].start
        sections.append(
            TopicSection(
                index=len(sections) + 1,
                start=g[0].start,
                end=g[-1].end,
                title=title,
                keywords=hits,
                text=(text[:300] + "…") if len(text) > 300 else text,
                score=min(1.0, 0.35 + 0.07 * len(hits) + min(0.25, duration / 600.0)),
            )
        )
    return sections


def write_paper_edit_csv(utterances: list[Utterance], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["index", "start", "end", "duration", "word_count", "confidence", "text"])
        for u in utterances:
            writer.writerow([u.index, f"{u.start:.3f}", f"{u.end:.3f}", f"{u.duration:.3f}", u.word_count, f"{u.confidence:.3f}", u.text])
    return out_path


def _candidate_recommendation(c: CutCandidate) -> tuple[str, str]:
    reason = c.reason or ""
    if c.kind == CutKind.PRODUCTION or "제작 현장" in reason or "재시작 안내" in reason:
        return "cut", "high"
    if c.kind == CutKind.LOW_INFO and c.duration <= 2.0:
        return "review", "medium"
    if c.kind == CutKind.FILLER:
        return "review", "low"
    return "review", "low"


def _context_for_candidate(c: CutCandidate, utterances: list[Utterance] | None, *, before: bool) -> str:
    if not utterances:
        return ""
    ordered = sorted(utterances, key=lambda u: (u.start, u.end))
    if before:
        prev = [u for u in ordered if u.end <= c.start + 1e-6]
        return prev[-1].text if prev else ""
    nxt = [u for u in ordered if u.start >= c.end - 1e-6]
    return nxt[0].text if nxt else ""


def write_review_decisions_csv(candidates: list[CutCandidate], out_path: Path, utterances: list[Utterance] | None = None) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "decision", "recommendation", "safety", "start", "end", "duration",
            "kind", "confidence", "reason", "context_before", "text", "context_after",
        ])
        for c in sorted(candidates, key=lambda x: (x.start, x.end)):
            recommendation, safety = _candidate_recommendation(c)
            writer.writerow([
                "", recommendation, safety, f"{c.start:.3f}", f"{c.end:.3f}", f"{c.duration:.3f}",
                c.kind, f"{c.confidence:.3f}", c.reason,
                _context_for_candidate(c, utterances, before=True), c.text, _context_for_candidate(c, utterances, before=False),
            ])
    return out_path


def write_director_html(
    *,
    source_name: str,
    out_path: Path,
    duration: float,
    utterances: list[Utterance],
    cuts: list[CutCandidate],
    review_candidates: list[CutCandidate],
    topics: list[TopicSection],
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    removed = sum(c.duration for c in cuts)
    rows = []
    for u in utterances:
        cut_hit = [c for c in cuts if c.start < u.end and c.end > u.start]
        review_hit = [c for c in review_candidates if c.start < u.end and c.end > u.start]
        cls = ""
        badge = ""
        if cut_hit:
            cls = " cut"
            badge = "AUTO CUT"
        elif review_hit:
            cls = " review"
            badge = "REVIEW"
        rows.append(f"<tr class='{cls.strip()}'><td>{u.index}</td><td>{_fmt_time(u.start)}</td><td>{_fmt_time(u.end)}</td><td>{u.duration:.1f}s</td><td>{html.escape(badge)}</td><td>{html.escape(u.text)}</td></tr>")
    review_rows = []
    for c in sorted(review_candidates, key=lambda x: (x.start, x.end)):
        recommendation, safety = _candidate_recommendation(c)
        review_rows.append(f"<tr><td>{_fmt_time(c.start)}</td><td>{_fmt_time(c.end)}</td><td>{html.escape(recommendation)}</td><td>{html.escape(safety)}</td><td>{html.escape(c.kind)}</td><td>{c.confidence:.2f}</td><td>{html.escape(c.reason)}</td><td>{html.escape(c.text)}</td></tr>")
    topic_rows = [f"<tr><td>{t.index}</td><td>{_fmt_time(t.start)}–{_fmt_time(t.end)}</td><td>{html.escape(t.title)}</td><td>{html.escape(', '.join(t.keywords))}</td></tr>" for t in topics]
    css = """body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:24px;line-height:1.5;color:#111;background:#fafafa}h1,h2{margin-top:28px}table{border-collapse:collapse;width:100%;background:white}th,td{border:1px solid #ddd;padding:8px;vertical-align:top}th{background:#f1f1f1}.cut{background:#fff0f0}.review{background:#fff8df}.summary{display:flex;gap:12px;flex-wrap:wrap}.card{background:white;border:1px solid #ddd;border-radius:10px;padding:14px;min-width:180px}code{background:#eee;padding:2px 4px;border-radius:4px}.note{background:#eef6ff;border:1px solid #cfe6ff;border-radius:10px;padding:12px}"""
    html_text = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>KoCut Director Review</title><style>{css}</style></head><body>
<h1>KoCut Director Review — {html.escape(source_name)}</h1>
<div class="summary"><div class="card"><b>원본</b><br>{duration:.1f}s</div><div class="card"><b>자동 컷</b><br>{len(cuts)}개 / {removed:.1f}s</div><div class="card"><b>리뷰 후보</b><br>{len(review_candidates)}개</div><div class="card"><b>문장 단위</b><br>{len(utterances)}개</div></div>
<div class="note"><b>v0.9 정책</b><br>이제는 기본 삭제어로 자동 컷합니다. 근데/그래서/그리고/그런데는 연결어로 보호합니다. 제작 현장 멘트는 자동 컷으로 분리합니다.</div>
<p><code>*.review_decisions.csv</code>의 decision 열에 <b>cut</b> 또는 <b>keep</b>을 적고, <code>kocut apply-decisions</code>로 EDL을 다시 만들 수 있습니다.</p>
<h2>토픽/챕터 후보</h2><table><thead><tr><th>#</th><th>구간</th><th>제목</th><th>키워드</th></tr></thead><tbody>{''.join(topic_rows) or '<tr><td colspan="4">없음</td></tr>'}</tbody></table>
<h2>리뷰 후보</h2><table><thead><tr><th>시작</th><th>끝</th><th>추천</th><th>안전도</th><th>종류</th><th>신뢰도</th><th>이유</th><th>텍스트</th></tr></thead><tbody>{''.join(review_rows) or '<tr><td colspan="8">없음</td></tr>'}</tbody></table>
<h2>Paper edit</h2><table><thead><tr><th>#</th><th>시작</th><th>끝</th><th>길이</th><th>상태</th><th>텍스트</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
</body></html>"""
    out_path.write_text(html_text, encoding="utf-8")
    return out_path
