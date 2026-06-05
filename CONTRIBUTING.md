# Contributing to KoCut

Thanks for your interest in KoCut.

KoCut is an early-stage open-source project for Korean video creators, editors, clinics, agencies, interview channels, and small production teams. The goal is to provide a local-first workflow for Korean transcription, cut suggestion, and export to editing tools.

## Development setup

```bash
git clone https://github.com/verabredung2021-cpu/kocut.git
cd kocut
python -m pip install --upgrade pip
pip install -e ".[dev]" || pip install -e .
python -m pytest
```

## Good contribution areas

- Korean filler-word detection rules
- Silence detection tests
- Retake detection improvements
- Shorts candidate detection
- Premiere Pro EDL import compatibility
- DaVinci Resolve import compatibility
- FCPXML export improvements
- Windows CUDA setup documentation
- Example exports and documentation
- Security review for file path handling and subprocess calls

## Pull request checklist

Before opening a pull request:

- Run `python -m pytest`
- Keep changes focused
- Add or update tests when possible
- Update README, CHANGELOG, ROADMAP, or docs if behavior changes
- Avoid committing private video, audio, patient/client data, API keys, or local machine paths

## Project direction

KoCut aims to remain local-first. Optional AI/API features may be added later, but core media processing should work without requiring a paid LLM API.
