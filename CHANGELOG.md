# Changelog

## v0.9.1 - v0.6 practical workflow merge

### Added
- Add Premiere Pro FCP7 XML export (`*.premiere.xml`, xmeml) on top of v0.9 editorial-brain.
- Add `kocut preview` for low-resolution MP4 review from a KoCut EDL before importing into an NLE.
- Restore EDL keep-range parsing with source start timecode offset support.

### Fixed
- Protect very short real utterances at the beginning/end of a video from being dropped by adjacent silence cuts.
- Remove unused `librosa` dependency; silence fallback remains `soundfile + numpy`.

### Kept from v0.9
- Korean connector protection: `근데`, `그래서`, `그리고`, `그런데` are not automatic filler cuts.
- User policy: `이제` remains a default delete word.
- Director workflow: paper edit, HTML review, review decisions CSV, and `apply-decisions`.


## v0.8.0 - Director paper-edit workflow

### Added
- Sentence/utterance-level director planner.
- `*.paper_edit.csv` export.
- `*.director_review.html` browser review page.
- `*.review_decisions.csv` for manual review decisions.
- `kocut apply-decisions` command to create a new EDL from reviewed CSV decisions.
- Meta JSON fields: `utterances`, `topic_sections`, `review_candidates`.
- GUI downloads for paper edit, review decisions, and HTML review.

### Changed
- `process` defaults to v0.8 sentence-level director mode. Use `--word-gap-mode` to compare with the v0.7 planner.

### Verified
- `pytest`: 112 passed.
- `compileall`: passed.
- CLI help for `process` and `apply-decisions`: passed.

## v0.9.0 - editorial brain

### Changed
- Protect Korean discourse connectors from automatic filler cuts: `근데`, `그래서`, `그리고`, `그런데`, and related connectors are no longer cut by default.
- Treat `이제` as a user-preferred default delete word and keep it through cut budgeting.
- Split production chatter into high-confidence automatic cuts, including shooting/setup/restart/editorial handoff phrases.
- Improve review CSV with recommendation, safety, and before/after context columns.
- Improve topic sectioning using question boundaries and fertility/medical keywords.
- Replace the librosa fallback silence scanner with soundfile+numpy to avoid Python 3.13/librosa/numba stalls.

### Fixed
- Fixed a possible runtime error in director-mode silence planning.
- Reduced false retake candidates for normal explanatory phrases such as `아니지만` and `아니잖아요`.
