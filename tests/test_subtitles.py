"""자막 분할 테스트."""
from __future__ import annotations

from kocut.subtitles import SplitOptions, split_subtitles
from kocut.types import Word


def _w(word: str, start: float, end: float) -> Word:
    return Word(word=word, start=start, end=end, prob=0.9)


def test_empty_input() -> None:
    assert split_subtitles([]) == []


def test_single_short_utterance() -> None:
    words = [_w("안녕하세요", 0.0, 1.0)]
    subs = split_subtitles(words)
    assert len(subs) == 1
    assert subs[0].text == "안녕하세요"
    assert subs[0].start == 0.0
    assert subs[0].end == 1.0


def test_breaks_on_final_ending() -> None:
    # 종결어미 '습니다' 뒤에서 끊기고 새 문장 시작
    words = [
        _w("오늘은", 0.0, 0.5),
        _w("날씨가", 0.5, 1.0),
        _w("좋습니다", 1.0, 1.8),
        _w("그래서", 2.2, 2.7),
        _w("산책을", 2.7, 3.2),
        _w("했어요", 3.2, 3.9),
    ]
    subs = split_subtitles(words)
    # 최소 2개 자막으로 나뉘어야 함
    assert len(subs) >= 2
    # 첫 자막은 '좋습니다'에서 끝남
    assert subs[0].text.endswith("좋습니다")


def test_force_split_on_max_words() -> None:
    # 종결어미 없이 어절만 많을 때 강제 분할
    words = [_w(f"단어{i}", i * 0.3, i * 0.3 + 0.3) for i in range(20)]
    subs = split_subtitles(words, SplitOptions(max_words=8, max_duration=99, max_chars=999))
    assert len(subs) >= 2
    # 각 자막의 어절 수가 max_words 이하
    for sub in subs:
        word_count = len(sub.text.split())
        assert word_count <= 8


def test_force_split_on_duration() -> None:
    words = [_w(f"말{i}", i * 1.0, i * 1.0 + 1.0) for i in range(10)]
    subs = split_subtitles(words, SplitOptions(max_words=99, max_duration=2.5, max_chars=999))
    assert len(subs) >= 2
    for sub in subs:
        assert (sub.end - sub.start) <= 3.6  # 약간의 여유


def test_indices_are_sequential() -> None:
    words = [
        _w("첫째", 0.0, 0.5), _w("문장입니다", 0.5, 1.3),
        _w("둘째", 1.8, 2.3), _w("문장입니다", 2.3, 3.1),
        _w("셋째", 3.6, 4.1), _w("문장입니다", 4.1, 4.9),
    ]
    subs = split_subtitles(words)
    indices = [s.index for s in subs]
    assert indices == list(range(1, len(subs) + 1))


def test_dependent_noun_not_isolated() -> None:
    # 의존명사 "수"가 앞 단어와 분리되어 자막 맨 앞에 오면 안 됨
    words = [_w("보호할", 0.0, 1.0), _w("수", 1.0, 1.3), _w("있을까요", 1.3, 2.0)]
    subs = split_subtitles(words)
    for sub in subs:
        assert not sub.text.startswith("수 ")
        assert sub.text.strip() != "수"


def test_dependent_noun_kept_under_force_split() -> None:
    # 강제 분할이 일어나는 긴 발화에서도 의존명사는 앞 단어와 묶임
    words = [_w(f"내용{i}", i * 0.6, i * 0.6 + 0.6) for i in range(12)]
    words += [_w("이용할", 7.2, 7.8), _w("수", 7.8, 8.1), _w("있어요", 8.1, 8.9)]
    subs = split_subtitles(words)
    for sub in subs:
        assert not sub.text.startswith("수 ")
