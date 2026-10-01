## 1. GUI Contract

- [x] 1.1 Add a centralized, conservative localization helper for detection screening severity.
- [x] 1.2 Expand the detection result table to five columns and disclose the screening-only semantics with tooltips.
- [x] 1.3 Populate severity for findings and neutral placeholders for no-detection and failed rows.
- [x] 1.4 Extend detection-result CSV export to include the displayed severity column.
- [x] 1.5 Replace image-local finding indices in the visible “编号” column with table-wide one-based sequence numbers without mutating evidence data.

## 2. Workflow Control

- [x] 2.1 Preserve the primary button's “停止” running-state appearance immediately after report confirmation.
- [x] 2.2 Enforce the same running-state appearance at the construction-plan generation entrypoint without changing the existing cancellation contract.
- [x] 2.3 Allow checked, engineer-confirmed `hold` plans to continue into non-release repair preview rendering while keeping evidence-inconsistent plans blocked.

## 3. Verification

- [x] 3.1 Update GUI contract tests for the five-column header and type-filter compatibility.
- [x] 3.2 Add tests for recognized, missing, unknown, no-detection and failed severity values.
- [x] 3.3 Add an export regression test that verifies the severity header and localized value.
- [x] 3.4 Add report-to-plan handoff tests for the primary button's text, style and disabled state.
- [x] 3.5 Add multi-image and multi-finding tests for continuous display numbering and CSV consistency.
- [x] 3.6 Add render-continuation tests for confirmed `hold`, unchecked optional rendering, and evidence-inconsistent blocking.
- [x] 3.7 Run focused GUI tests, strict OpenSpec validation, and an offscreen visual check of the detection result panel.
