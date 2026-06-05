# KoCut Roadmap

KoCut is an early-stage local video editing assistant for Korean creators, editors, clinics, agencies, interview channels, and small production teams.

The project is local-first: core video/audio analysis should work without requiring a paid LLM API. Optional AI/API features may be added later for maintainer workflows and natural-language editing commands.

## v0.4.x

- Improve Korean filler-word detection rules
- Improve retake detection for interview and clinic videos
- Add more tests for silence, fillers, retakes, Shorts candidates, and exports
- Validate EDL import behavior in Premiere Pro
- Validate timeline import behavior in DaVinci Resolve
- Improve FCPXML compatibility
- Add media-free example outputs
- Improve Windows CUDA, FFmpeg, and Whisper setup documentation
- Review file path handling and subprocess safety

## v0.5.x

- Add timeline preview
- Add project presets for YouTube, Shorts, interviews, clinics, lectures, and podcasts
- Add safer batch processing
- Add better export profiles for Premiere Pro and DaVinci Resolve
- Add structured review reports for editors
- Improve logging and error diagnostics
- Add optional natural-language editing commands such as "cut this to 8 minutes" or "find Shorts candidates"

## v0.6.x and later

- Improve Korean speech and filler analysis quality
- Add plugin or companion workflows for professional editing tools
- Add project-level configuration files
- Add stronger dependency and security review automation
- Add benchmark examples for Korean editing workflows
