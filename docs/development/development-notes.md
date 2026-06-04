# Development Notes

This document tracks implementation notes and maintainer priorities.

## Current focus

- Keep the core workflow local-first
- Improve Korean editing-specific rules
- Keep export formats easy to inspect and modify
- Avoid committing private media or machine-specific paths
- Improve test coverage before adding complex features

## Safety notes

Areas that need careful review:

- User-provided input paths
- Output paths and overwrite behavior
- Temporary file handling
- FFmpeg and external command execution
- GUI input validation
- Exported XML/JSON/EDL/SRT escaping
