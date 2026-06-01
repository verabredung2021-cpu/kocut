# KoCut — 한국어 영상 자동 편집 보조 도구

영상 파일을 넣으면 한국어 자막을 만들고, **무음·간투사·재촬영** 같은 컷 후보를 자동으로 뽑아주는 도구입니다. 결과를 Premiere Pro / DaVinci Resolve에 import해서 검토 후 적용하는 워크플로입니다.

## 왜 이 도구인가

이 도구의 전신은 Premiere CEP 패널이었지만, Adobe CEP 환경(ExtendScript / 캐시 / 통신 규약)의 복잡성 때문에 안정적으로 동작하지 못했습니다. KoCut은 그 교훈을 반영해 **Premiere와 완전히 분리된 독립 도구**로 만들었습니다.

- ✅ Adobe SDK / CEP / ExtendScript 의존 **없음** — Premiere가 깨질 일 없음
- ✅ LLM API 비용 **0원** — faster-whisper + Kiwi 형태소 분석 + 규칙 기반
- ✅ 표준 파일(SRT/EDL)로 출력 — Premiere·DaVinci·FCP·곰믹스 등 어디서나 import
- ✅ 원본 영상을 **수정하지 않음** — 분석만, 안전

## 기능

| 기능 | 설명 |
|---|---|
| 한국어 트랜스크립션 | faster-whisper (large-v3), 단어 단위 타임스탬프 |
| 자막 분할 | Kiwi 형태소 분석 — 종결어미/조사 기준 호흡 단위 분할 |
| 간투사 검출 | 어/음/그/저 등을 컷 후보로 (의문문 '어?'는 제외) |
| 무음 검출 | RMS 기반, 발화 구간 보호(배경음악 오인식 방지) |
| 재촬영 검출 | NG 마커 + 반복 발화 유사도 |
| 쇼츠 후보 | 한국어 훅·감정 키워드 점수로 9:16 구간 추천 |

## 시스템 요구사항

- Python 3.10 이상 (3.12 권장)
- FFmpeg (PATH에 등록)
  - Windows: `winget install Gyan.FFmpeg`
  - macOS: `brew install ffmpeg`
- GPU 권장 (없어도 동작하지만 느림)

## 설치

```bash
# 1. 코드 받기 (압축 해제한 폴더로 이동)
cd kocut

# 2. 가상환경 + 의존성 (uv 권장 — 빠르고 충돌 적음)
pip install uv
uv venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

uv pip install -e .

# uv 없이 일반 pip로도 가능
pip install -e .
```

첫 실행 시 Whisper 모델(large-v3, 약 3GB)이 자동 다운로드되어 `~/.cache/huggingface/`에 저장됩니다. 두 번째 실행부터는 빠릅니다.

## 사용법

### GUI (권장 — 쉬움)

```bash
# GUI 추가 설치 (최초 1회)
uv pip install -e ".[gui]"

# 실행 → 브라우저가 자동으로 열림
python -m kocut.gui
```

브라우저에서:
1. 영상 경로를 붙여넣거나(큰 영상 권장) 파일을 선택
2. 모델·옵션 확인 후 **분석 시작** 클릭
3. 자막 / 컷 후보 / 쇼츠 후보 탭에서 결과 확인
4. SRT·EDL·JSON 내려받기 → Premiere/DaVinci에 import

### CLI

```bash
# 기본 — 영상 분석 후 같은 폴더에 결과 생성
python -m kocut video.mp4

# 출력 폴더 지정
python -m kocut video.mp4 -o ./output

# 더 빠른 모델 사용 (정확도 약간 낮음)
python -m kocut video.mp4 -m large-v3-turbo

# 특정 검출 끄기
python -m kocut video.mp4 --skip-retakes --skip-shorts

# 상세 로그
python -m kocut video.mp4 -v
```

### 생성되는 파일

`video.mp4`를 처리하면 다음이 생깁니다:

- `video.srt` — 한국어 자막
- `video.cuts.edl` — 컷 후보를 반영한 편집 결정 리스트(남길 구간만)
- `video.meta.json` — 모든 분석 데이터 (자막/컷/쇼츠/세그먼트)
- `video.log` — 처리 로그

## Premiere Pro에서 사용하기

**자막 넣기 (SRT):**
1. 파일 → 가져오기 → `video.srt` 선택
2. 프로젝트 패널에서 자막을 타임라인으로 드래그
3. 자막이 캡션 트랙으로 들어감

**컷 적용하기 (EDL):**
1. 파일 → 가져오기 → `video.cuts.edl` 선택
2. EDL이 새 시퀀스로 열림 — 무음/간투사/재촬영을 뺀 '남길 구간'만 이어진 상태
3. 원본 영상을 이 시퀀스에 연결(relink)하면 자동 편집본 완성
4. 검토 후 직접 다듬기

**DaVinci Resolve도 동일** — 파일 → Import → Timeline → EDL.

> 참고: 컷 후보는 어디까지나 **제안**입니다. JSON의 `cuts` 배열에 각 컷의 이유(`reason`)와 신뢰도(`confidence`)가 있으니, 자체 검토 후 적용하세요.

## 트러블슈팅

| 증상 | 해결 |
|---|---|
| `'ffmpeg'를 찾을 수 없습니다` | FFmpeg 설치 후 PATH 등록. 터미널 새로 열기 |
| `faster-whisper가 설치되지 않았습니다` | `pip install faster-whisper` |
| `cublas64_12.dll is not found` 등 GPU 오류 | 기본값(auto)이면 자동으로 CPU로 전환됩니다. GPU 가속을 쓰려면 `uv pip install nvidia-cublas-cu12 nvidia-cudnn-cu12`. 또는 장치를 `cpu`로 지정 (`--device cpu` 또는 GUI 장치 선택) |
| 트랜스크립션이 매우 느림 | GPU 없으면 CPU로 동작(느림). `-m large-v3-turbo` 로 속도 개선 |
| 모델 다운로드 실패 | 네트워크/방화벽 확인. HuggingFace 접근 필요 |
| 한국어가 다른 언어로 인식됨 | 이미 `language="ko"` 고정이라 발생하지 않음. 발생 시 이슈 등록 |
| 간투사가 과검출됨 | `kocut/fillers.py`의 `_FILLER_WORDS` / `_MAX_FILLER_DURATION` 조정 |

## 동작 검증 (개발자용)

```bash
pip install -e ".[dev]"
pytest -v
```

룰베이스 모듈(자막·간투사·무음·재촬영·쇼츠·출력)은 ML 모델 없이 전부 테스트됩니다.

## 구조

```
kocut/
  audio.py         # FFmpeg 래퍼 (오디오 추출)
  transcribe.py    # faster-whisper 래퍼 (한국어 고정)
  subtitles.py     # Kiwi 형태소 기반 자막 분할
  fillers.py       # 간투사 검출
  silence.py       # 무음 검출 (발화 보호)
  retakes.py       # 재촬영/NG 검출
  shorts.py        # 쇼츠 후보 점수
  output.py        # SRT / EDL / JSON 출력
  cli.py           # 명령줄 진입점
```

## 로드맵

- **v0.1**: CLI — 자막 + 컷 후보 추출 ✅
- **v0.2 (현재)**: Gradio GUI — 드래그앤드롭/경로 입력, 결과 탭, 다운로드 ✅ ← **지금 여기**
- v0.3: 한국어 자연어 명령 ("8분으로 줄여줘", "쇼츠 만들기")
- v0.4: EDL/XML export 고도화 (드롭프레임, 멀티트랙)
- v0.5: 멀티캠 화자 분리(pyannote), 썸네일 이미지 처리

## 라이선스 / 사용 모델

- faster-whisper (MIT), Kiwi (LGPL), librosa (ISC), rapidfuzz (MIT)
- Whisper 모델: OpenAI (MIT). 한국어 fine-tune 모델 사용 시 `-m ghost613/whisper-large-v3-turbo-korean`
