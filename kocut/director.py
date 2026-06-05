"""문장 단위 rough-cut 플래너.

v0.8의 목표는 단순히 gap을 많이 자르는 것이 아니라, 편집자가 검수할 수 있는
"문장 단위 paper edit"를 만드는 것입니다. 컷백류 도구가 강한 이유는 무음 자체보다
말의 단위와 재촬영/말실수 후보를 묶어 보여주는 워크플로에 있으므로, 이 모듈은
다음을 제공합니다.

- Whisper word timestamps → 문장/호흡 단위 Utterance 생성
- 문장 사이 gap만 대상으로 하는 무음 컷 후보 생성
- 반복 발화, NG 마커, filler cluster 등 리뷰 후보 생성
- paper edit CSV / review CSV / HTML 리뷰 출력

모든 로직은 로컬 규칙 기반입니다. LLM·인터넷 호출 없이 결정적으로 동작합니다.
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
_GENERIC_LOW_INFO = {"네", "네네", "자", "그러면", "그럼", "좋습니다", "좋아요", "알겠습니다", "오케이", "오케이요", "이제", "자 이제"}
_FILLER_WORDS = {"어", "음", "아", "그", "저", "뭐", "막", "약간", "이제", "저기", "흠"}
_TOPIC_KEYWORDS = (
    "결론", "핵심", "중요", "문제", "이유", "방법", "수술", "치료", "검사", "증상",
    "원인", "비용", "회복", "부작용", "위험", "장점", "단점", "예방", "관리", "진단",
    "자궁", "난소", "임신", "호르몬", "생리", "통증", "초음파", "시술", "상담",
)
_GENERIC_LOW_INFO_NORMALISED = set()


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


def detect_review_candidates(utterances: list[Utterance], words: list[Word] | None = None) -> list[CutCandidate]:
    """자동 컷하지 않고 사람이 확인해야 할 후보를 검출합니다."""
    candidates: list[CutCandidate] = []
    units = sorted(utterances, key=lambda u: (u.start, u.end))
    for u in units:
        text = u.text.strip()
        norm = _normalise_for_match(text)
        lowered = text.lower()
        for marker in _RETAKE_MARKERS:
            if marker.lower() in lowered:
                candidates.append(CutCandidate(start=u.start, end=u.end, kind=CutKind.RETAKE, reason=f"NG/재촬영 마커 후보: {marker}", text=text, confidence=0.78))
                break
        else:
            if any(m in text for m in _STUMBLE_MARKERS):
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
            ws = [w for w in words if w.start >= u.start - 1e-6 and w.end <= u.end + 1e-6]
            if not ws:
                continue
            filler_count = sum(_clean_word(w.word).rstrip("?!.,…") in _FILLER_WORDS for w in ws)
            ratio = filler_count / max(1, len(ws))
            if filler_count >= 2 and ratio >= 0.30 and u.duration <= 5.0:
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


def build_topic_sections(utterances: list[Utterance], *, max_gap: float = 8.0, min_duration: float = 20.0) -> list[TopicSection]:
    units = sorted(utterances, key=lambda u: (u.start, u.end))
    if not units:
        return []
    groups: list[list[Utterance]] = []
    cur: list[Utterance] = []
    for i, u in enumerate(units):
        if cur and (u.start - cur[-1].end) >= max_gap and (cur[-1].end - cur[0].start) >= min_duration:
            groups.append(cur)
            cur = []
        cur.append(u)
        if i == len(units) - 1 and cur:
            groups.append(cur)
    sections: list[TopicSection] = []
    for g in groups:
        text = " ".join(u.text for u in g)
        hits = [kw for kw in _TOPIC_KEYWORDS if kw in text]
        title_base = g[0].text.strip()
        title = title_base[:40] + ("…" if len(title_base) > 40 else "")
        if hits:
            title = f"{', '.join(hits[:3])} — {title}"
        sections.append(TopicSection(index=len(sections) + 1, start=g[0].start, end=g[-1].end, title=title, keywords=hits[:8], text=(text[:240] + "…") if len(text) > 240 else text, score=min(1.0, 0.35 + 0.08 * len(hits) + min(0.25, (g[-1].end - g[0].start) / 600.0))))
    return sections


def write_paper_edit_csv(utterances: list[Utterance], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["index", "start", "end", "duration", "word_count", "confidence", "text"])
        for u in utterances:
            writer.writerow([u.index, f"{u.start:.3f}", f"{u.end:.3f}", f"{u.duration:.3f}", u.word_count, f"{u.confidence:.3f}", u.text])
    return out_path


def write_review_decisions_csv(candidates: list[CutCandidate], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["decision", "start", "end", "duration", "kind", "confidence", "reason", "text"])
        for c in sorted(candidates, key=lambda x: (x.start, x.end)):
            writer.writerow(["", f"{c.start:.3f}", f"{c.end:.3f}", f"{c.duration:.3f}", c.kind, f"{c.confidence:.3f}", c.reason, c.text])
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
    review_rows = [f"<tr><td>{_fmt_time(c.start)}</td><td>{_fmt_time(c.end)}</td><td>{html.escape(c.kind)}</td><td>{c.confidence:.2f}</td><td>{html.escape(c.reason)}</td><td>{html.escape(c.text)}</td></tr>" for c in sorted(review_candidates, key=lambda x: (x.start, x.end))]
    topic_rows = [f"<tr><td>{t.index}</td><td>{_fmt_time(t.start)}–{_fmt_time(t.end)}</td><td>{html.escape(t.title)}</td><td>{html.escape(', '.join(t.keywords))}</td></tr>" for t in topics]
    css = """body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:24px;line-height:1.5;color:#111;background:#fafafa}h1,h2{margin-top:28px}table{border-collapse:collapse;width:100%;background:white}th,td{border:1px solid #ddd;padding:8px;vertical-align:top}th{background:#f1f1f1}.cut{background:#fff0f0}.review{background:#fff8df}.summary{display:flex;gap:12px;flex-wrap:wrap}.card{background:white;border:1px solid #ddd;border-radius:10px;padding:14px;min-width:180px}code{background:#eee;padding:2px 4px;border-radius:4px}"""
    html_text = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>KoCut Director Review</title><style>{css}</style></head><body>
<h1>KoCut Director Review — {html.escape(source_name)}</h1>
<div class="summary"><div class="card"><b>원본</b><br>{duration:.1f}s</div><div class="card"><b>자동 컷</b><br>{len(cuts)}개 / {removed:.1f}s</div><div class="card"><b>리뷰 후보</b><br>{len(review_candidates)}개</div><div class="card"><b>문장 단위</b><br>{len(utterances)}개</div></div>
<p><code>*.review_decisions.csv</code>의 decision 열에 <b>cut</b> 또는 <b>keep</b>을 적고, <code>kocut apply-decisions</code>로 EDL을 다시 만들 수 있습니다.</p>
<h2>토픽/챕터 후보</h2><table><thead><tr><th>#</th><th>구간</th><th>제목</th><th>키워드</th></tr></thead><tbody>{''.join(topic_rows) or '<tr><td colspan="4">없음</td></tr>'}</tbody></table>
<h2>리뷰 후보</h2><table><thead><tr><th>시작</th><th>끝</th><th>종류</th><th>신뢰도</th><th>이유</th><th>텍스트</th></tr></thead><tbody>{''.join(review_rows) or '<tr><td colspan="6">없음</td></tr>'}</tbody></table>
<h2>Paper edit</h2><table><thead><tr><th>#</th><th>시작</th><th>끝</th><th>길이</th><th>상태</th><th>텍스트</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
</body></html>"""
    out_path.write_text(html_text, encoding="utf-8")
    return out_path
