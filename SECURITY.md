# Security Policy

KoCut is a local video editing assistant that processes user-selected media files and exports editing metadata such as SRT, EDL, FCPXML, JSON, and Markdown.

## Supported versions

Security reviews and fixes focus on the latest public release.

| Version | Supported |
| --- | --- |
| v0.4.x | Yes |
| older versions | Best effort |

## Security areas of interest

Because KoCut handles arbitrary local file paths, filenames, media files, and export destinations, the project pays special attention to:

- Path traversal and unsafe file writes
- Unsafe subprocess usage around FFmpeg and related tools
- Dependency vulnerabilities
- Temporary file handling
- GUI and local API input validation
- Export safety for SRT, EDL, FCPXML, JSON, and Markdown files

## Reporting a vulnerability

Please use GitHub private security advisories if available. If that is not available, contact the maintainer through the GitHub profile.

Please include:

- A clear description of the issue
- Steps to reproduce
- Affected version or commit
- Example input if safe to share
- Expected and actual behavior

Please do not publicly disclose a vulnerability until there has been a reasonable opportunity to investigate and release a fix.
