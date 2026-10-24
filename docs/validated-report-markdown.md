# Validated Markdown artifacts: USE-004 / RUN-103 increment

Implemented 25 October 2026. An artifact node may select
`config.reportFormat: "business-report-markdown.v1"` with a `.md` artifact name.
Its default content is `{{ lastOutput }}` and its canonical metadata media type is
`text/markdown; charset=utf-8`. The source is the canonical
[`business-report.v1` JSON](./business-output-contracts.md), rather than a new model
generation. The format survives publication and BPMN round trips.

The renderer deterministically builds summary, claims, calculation tables,
limitations and a description of the validation boundary. It escapes model text
as literal Markdown, so embedded HTML, images, headings and supplied links cannot
become active report formatting. Citation markers are generated only from the
validated references. The source appendix opens the exact allowed document/chunk
and includes original provenance hashes and the retained source fragment.

Immediately before saving, the existing output validator checks schema, arithmetic,
current source hashes/scope and referenced artifact availability/bytes again.
Missing or ambiguous sources, changed evidence, insufficient output, unavailable
validation and an export exceeding 800,000 UTF-8 bytes stop the process before
the report artifact is written. There is no silent truncation or replacement model
call. This matters when a report waited for human review before export.

The stored artifact and artifact-stage output contain exactly the same rendered
Markdown. Canonical process `lastOutput` remains the structured result, so later
JSON conditions keep their input. The existing download route sends an attachment
as `application/octet-stream`; its bytes and SHA-256 match the stored Markdown
metadata. A render receipt records only format/schema/input/content hashes,
size and the factual-review boundary. Rejection records bounded reason codes,
not document/report payload or raw upstream errors.

The format itself does not grant approval or claim human acceptance. Add the
existing approval node before the final artifact when the pack requires human
review. Tests exercise agent validation → human decision → artifact: rejection
produces no artifact, and source drift during the wait blocks export. Generic
single-node artifact preview cannot establish run-scoped evidence and gives an
explicit error; use a complete process for this validation.

Verification covers deterministic bytes/hashes, safe Markdown rendering, formula
tables, exact citation targets, media/config validation, BPMN, approval/rejection,
changed/ambiguous sources, payload limits, and real HTTP attachment bytes. Legacy
scheduler/process/internal-report v1 tests remain the compatibility gate.
These are synthetic checks; factual correctness and a real business owner's
acceptance remain independent qualification requirements.

Remaining USE-004 scope includes in-app rich Markdown presentation, DOCX/PDF from
the same canonical content, and a persisted accept/rework decision tied to exact
artifact version/run. Installing this formatter does not close the full epic.
