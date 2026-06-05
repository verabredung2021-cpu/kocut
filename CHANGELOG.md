
## v0.4.1 - GPU DLL loading fix

### Fixed
- Improved Windows GPU DLL discovery for CTranslate2 / faster-whisper.
- Automatically registers NVIDIA CUDA package DLL directories from the active virtual environment.
- Adds both os.add_dll_directory() and PATH registration for cuBLAS/cuDNN runtime DLLs.
- Reduces false CPU fallback when cublas64_12.dll exists inside .venv\Lib\site-packages\nvidia\...\bin.

### Notes
- This release focuses on CUDA runtime loading reliability on Windows.
- If GPU still fails with a DLL "cannot be loaded" error, check NVIDIA driver CUDA compatibility with 
vidia-smi.

# Changelog


## v0.4.2

- Continued KoCut public development.
- Updated project version to v0.4.2.
- Refined OSS maintenance, documentation, and release workflow.
All notable changes to KoCut will be documented in this file.

## Unreleased

### Added

- GitHub Actions CI workflow
- Issue templates for bugs, features, docs, and security review
- Pull request template
- Dependabot configuration
- Roadmap, contributing guide, security policy, support guide, and maintainer notes
- Media-free example export files

### Changed

- Improved open-source project structure for public collaboration and review

## v0.4.0

### Added

- Continued public development of Korean video auto-cut workflow
- Export workflow documentation for editor-reviewable outputs
- Project documentation updates for OSS review

## v0.2.x

### Added

- Initial public development series
- CLI workflow
- Gradio GUI
- Korean subtitle generation workflow
- Silence, filler-word, retake, and Shorts candidate logic
- SRT, EDL, JSON, and Markdown-style exports
- Rule-based tests


