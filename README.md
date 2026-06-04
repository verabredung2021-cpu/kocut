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


## v0.4.1 패치 내용

- **Windows GPU(cuda) DLL 로딩 수정.** `nvidia-cublas-cu12` / `nvidia-cudnn-cu12`를 설치해도 ctranslate2가 `cublas64_12.dll`을 못 찾던 문제. 원인은 (1) venv에서 nvidia 패키지 경로 탐색이 불완전, (2) `os.add_dll_directory`만으로는 cuDNN→cuBLAS 전이 의존성이 안 잡힘. 이제 `nvidia` 네임스페이스 패키지 위치를 importlib로 정확히 찾고, DLL 디렉토리를 `add_dll_directory` + `PATH` 양쪽에 등록합니다. DLL을 못 찾으면 `-v` 로그에 안내를 남깁니다.

## v0.4.0 패치 내용

내부 구조를 정리하고(파이프라인 통합) 0.3에서 남겨둔 한계를 닫았습니다.

- **ffprobe 자동 감지.** 원본에서 fps·해상도·시작 타임코드를 읽습니다. `--fps`를 생략하면 **원본 fps를 자동 사용**(예: 23.976), FCPXML 해상도도 원본 그대로(4K면 4K)로 들어갑니다.
- **EDL 시작 타임코드 보정.** 원본이 0이 아닌 임베디드 TC(예: Sony XAVC의 `01:00:00:00`)를 가지면 소스 타임코드에 그만큼 오프셋을 더해, TC 기준 relink에서도 컷이 어긋나지 않습니다.
- **컷 미리보기 리포트(`{영상}.cuts.md`).** NLE에 넣기 전에 "무엇이 어디서 왜, 총 몇 초 잘리는지"를 한눈에 검토. auto-editor식 미리보기 리포트.
- **간투사 3단계 모드(`--filler-mode`).** `conservative`(핵심 간투사만 자동 컷) / `balanced`(기본) / `aggressive`(애매한 것까지). 자동 컷에서 빠진 애매한 간투사는 리포트·GUI의 "검토 후보"로 표시 — 컷은 항상 deterministic, LLM 없음.
- **CLI·GUI 파이프라인 통합(`pipeline.analyze`).** 이전엔 GUI가 별도 파이프라인이라 0.3의 단어 경계 정제를 못 받았는데, 이제 양쪽이 같은 코드를 호출합니다. GUI도 FCPXML·미리보기·검토 후보 탭을 제공합니다.
- 첫 **파이프라인 통합 테스트** 추가. mypy strict clean + pytest 91개 통과.



오픈소스(auto-editor·CutScript·Silenci 등) 벤치마킹을 반영해 **컷 품질**과 **relink**를 끌어올렸습니다. 전부 룰베이스(결정적), LLM 없음.

- **단어 경계 컷 보정 (말 앞뒤 씹힘 방지) — 이번 핵심.** 기존엔 간투사 컷이 양쪽으로 80ms씩 *확장*돼 다음 실제 단어 앞부분을 잘라먹었습니다. 이제 출력 직전 정제 패스(`refine.py`)가 모든 컷을 '유지할 단어' 경계 안쪽으로 끌어와, 컷이 발화를 침범하지 않습니다.
- **컷 안정화 옵션** (auto-editor 스타일): `--pad-before-ms`(다음 발화 전 여유), `--pad-after-ms`(직전 발화 뒤 여유), `--min-cut-ms`(짧은 컷 무시), `--min-clip-ms`(짧은 남길 구간 제거, 기본 100ms).
- **FCPXML export 추가 (beta)** — `{영상}.fcpxml`. EDL 타임코드 대신 유리수 시간(예: `1001/24000s`)을 써서 23.976 등 분수 fps에서도 프레임 정확, 원본 경로를 직접 담아 Resolve/Premiere relink가 더 안정적입니다. `--skip-fcpxml`로 끌 수 있음. *실제 NLE import 검증 필요.*
- mypy strict clean + pytest 84개 통과 유지.

⚠️ FCPXML 알려진 한계(beta): 해상도는 1920×1080 기본값으로 적습니다(원본 해상도 ffprobe 반영은 v0.4). 원본 파일 경로/이름은 실행한 OS 기준으로 들어가므로, NLE/미디어와 같은 OS에서 생성하세요.



- **EDL 타임코드 정확도 수정 (relink 핵심).** 23.976·29.97 같은 분수 fps에서 프레임을 정수(24·30)로 반올림해 세던 탓에, 원본에 relink하면 컷이 시간이 갈수록 뒤로 밀렸습니다(16분에 약 24프레임≈1초, 30분에 43프레임). 이제 **실제 fps로 프레임을 세고** HH:MM:SS:FF 롤오버만 정수 베이스로 처리해 원본 프레임과 정확히 맞습니다. (drop-frame 라벨링은 non-drop 고정 — v0.4 로드맵.)
- EDL 각 이벤트에 DaVinci Resolve용 `* SOURCE FILE` 라인을 추가했습니다(Premiere용 `* FROM CLIP NAME`과 병행). relink 성공률을 높입니다.
- 두 컷 사이에 생기는 0.1초 미만의 '남길 조각'(마이크로 클립)을 자동 제거해 타임라인을 깔끔하게 만듭니다.
- 긴 영상 트랜스크립션에 **진행률 표시줄**을 추가했습니다(영상 길이 대비 %). 30분+ 영상에서 "멈춘 건지 도는 건지" 모호하던 문제를 해소합니다.
- `requires-python`을 `>=3.10,<3.13`으로 명시했습니다(3.13 미지원 반영).
- `mypy --strict` clean + pytest 76개 통과 유지(타입 정리 포함).



- `python -m kocut process video.mp4`와 `python -m kocut video.mp4`를 모두 지원하도록 CLI 진입점을 수정했습니다.
- 기존 실행 명령이 깨지지 않도록 `--compute-type`, `--keep-wav` 옵션을 추가했습니다.
- faster-whisper가 세그먼트 텍스트만 주고 word timestamp를 비워 둘 때 SRT가 빈 파일이 되던 문제를 세그먼트 단위 fallback으로 보강했습니다.
- EDL 트랙 표기를 `AA/V` 한 줄에서 `V` + `A` 두 줄로 바꿔 Premiere/DaVinci에서 오디오가 빠질 가능성을 줄였습니다.
- 한 프로세스에서 여러 번 실행할 때 로그 파일 핸들러가 추가되지 않던 문제를 수정했습니다.
- GUI에도 compute type 선택과 word timestamp fallback을 반영했습니다.

## v0.2.7 패치 내용

- 완전 무음 WAV가 무음으로 검출되지 않던 문제를 수정했습니다.
- 긴 무음 구간 안에 발화 타임스탬프가 있을 때 전체 무음을 버리지 않고, 발화 앞뒤 구간으로 나눠 검출합니다.
- Windows CUDA DLL 경로 등록 핸들을 유지해 `nvidia-cublas-cu12` / `nvidia-cudnn-cu12` 설치 후에도 DLL 검색 경로가 사라지지 않도록 했습니다.
- `kiwipiepy`가 없는 개발/테스트 환경에서도 import가 실패하지 않도록 최소 fallback 분석기를 추가했습니다. 실제 사용은 Kiwi 설치를 권장합니다.
- FPS가 0에 가깝거나 NaN인 경우, 중첩 출력 폴더가 없는 경우, 쇼츠 후보 개수가 0인 경우의 엣지케이스를 보강했습니다.

## 시스템 요구사항

- Python 3.10–3.12 (**3.12 권장**). 3.13은 librosa/numba 휠 호환 문제로 아직 미지원 — 무음 검출에서 멈춤/세그폴트가 보고됨.
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

# 기존 하위 명령 방식도 지원
python -m kocut process video.mp4

# 출력 폴더 지정
python -m kocut video.mp4 -o ./output

# 더 빠른 모델 사용 (정확도 약간 낮음)
python -m kocut video.mp4 -m large-v3-turbo

# 특정 검출 끄기
python -m kocut video.mp4 --skip-retakes --skip-shorts

# fps는 미지정 시 원본에서 자동 감지 (필요하면 --fps로 강제)
python -m kocut process video.mp4 --device cuda --compute-type float16 --keep-wav

# 컷 안정화: 발화 앞뒤 60ms 여유 + 0.15초 미만 컷 무시 (말 씹힘/자잘한 컷 방지)
python -m kocut process video.mp4 --pad-before-ms 60 --pad-after-ms 60 --min-cut-ms 150

# 간투사 보수적 모드 — 핵심('어/음')만 자동 컷, 애매한 건 .cuts.md '검토 후보'로
python -m kocut process video.mp4 --filler-mode conservative

# 상세 로그
python -m kocut video.mp4 -v
```

### 생성되는 파일

`video.mp4`를 처리하면 다음이 생깁니다:

- `video.srt` — 한국어 자막
- `video.cuts.edl` — 컷 후보를 반영한 편집 결정 리스트(남길 구간만, CMX3600)
- `video.fcpxml` — 컷 반영 FCPXML(beta). 프레임 정확·원본 경로 포함이라 relink가 더 안정적 (Resolve/Premiere)
- `video.cuts.md` — 컷 미리보기 리포트(제거 시간/결과 길이/컷 목록/검토 후보)
- `video.meta.json` — 모든 분석 데이터 (자막/컷/검토 후보/쇼츠/세그먼트)
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
  refine.py        # 단어 경계 컷 보정 + 패딩 + 최소 길이
  shorts.py        # 쇼츠 후보 점수
  output.py        # SRT / EDL / JSON 출력
  fcpxml.py        # FCPXML 출력 (relink용, beta)
  review.py        # 컷 미리보기 리포트 (.cuts.md)
  pipeline.py      # 공용 분석 파이프라인 (CLI·GUI 공통)
  cli.py           # 명령줄 진입점
  gui.py           # Gradio GUI (pipeline 호출)
```

## 로드맵

- **v0.1**: CLI — 자막 + 컷 후보 추출 ✅
- **v0.2**: Gradio GUI — 드래그앤드롭/경로 입력, 결과 탭, 다운로드 ✅
- **v0.3**: 단어 경계 컷 보정 + 컷 안정화 옵션 + FCPXML export(beta) ✅
- **v0.4 (현재)**: ffprobe 자동 감지(fps/해상도/시작 TC) + 컷 미리보기 리포트 + 간투사 3단계 모드 + CLI·GUI 파이프라인 통합 ✅ ← **지금 여기**
- v0.4.x (남음): 드롭프레임(29.97) 타임코드, OTIO export
- v0.5: 한국어 자연어 명령("8분으로 줄여줘"), transcript 검색/DB, 멀티캠 화자 분리(pyannote)

### 오픈소스 벤치마킹 메모 (참고만, 미구현)

경쟁 도구 분석 결과 장기적으로 참고할 방향(현재 의도적으로 범위 밖):

- **간투사 3단계 처리** (확실=자동 / 애매=후보 / 사용자 승인) — 실제 컷은 deterministic 유지, LLM은 "후보 분류"에만. 실footage 튜닝 후 도입.
- **transcript DB + 검색** (StoryToolkitAI류) — 촬영본별 전사 저장, "자궁근종 설명한 부분만" 검색. 영상이 많아지면 가치 큼.
- **단어 클릭 GUI 편집** (CutScript/Descript류) — Electron/React 필요, 개발량 큼.
- 범위를 "한국어 병원 유튜브 자동 컷 초벌기"로 좁게 유지하는 게 Cutback 복제보다 현실적.

## 라이선스 / 사용 모델

- faster-whisper (MIT), Kiwi (LGPL), librosa (ISC), rapidfuzz (MIT)
- Whisper 모델: OpenAI (MIT). 한국어 fine-tune 모델 사용 시 `-m ghost613/whisper-large-v3-turbo-korean`
