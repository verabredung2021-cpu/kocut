# Changelog

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
