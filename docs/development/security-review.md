# Security Review Notes

This file tracks non-sensitive security review tasks.

## File handling

- Validate input paths
- Avoid unsafe overwrites
- Avoid writing outside selected output directories
- Handle unusual filenames safely

## Subprocess handling

- Avoid shell execution when possible
- Pass arguments as lists
- Validate FFmpeg paths and media paths
- Capture and report errors safely

## Exports

- Escape text where needed
- Keep generated files inspectable
- Avoid embedding private absolute paths unless required by the target editor
