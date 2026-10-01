## Why

The damage-report review dialog exposes the complete v3 JSON document as its primary editing surface. This makes normal engineering review difficult to scan, shows implementation-only fields and `null` values, and invites accidental changes to evidence identity and provenance.

## What Changes

- Replace the default raw-JSON editor with a human-readable Chinese review form organized into overall information, damage findings, and review/limitations tabs.
- Render canonical damage levels as Chinese dropdown options while preserving the existing schema values when saving.
- Display optional values as blank fields, wrap long content, and present standards references as readable source rows instead of raw knowledge markers where metadata is available.
- Keep complete JSON available only in a read-only advanced-data tab synchronized from the form.
- Reconstruct and validate `DamageReport` from editable business fields while restoring immutable report version, provenance, integrity, and finding identities from the original report.
- Preserve draft persistence, human-confirmation validation, and the existing construction-plan handoff gate.

## Capabilities

### New Capabilities

- `human-friendly-report-review`: Structured damage-report review, immutable audit data, readable references, and read-only advanced data.

### Modified Capabilities


## Impact

- `runtime/damage_workflow_gui.py`: report-review dialog controls, form-to-model reconstruction, and advanced-data rendering.
- `tests/test_report_review_dialog_handoff.py`: structured editing, mapping, persistence, and immutability coverage.
- `tests/test_gui_visual_smoke.py`: responsive tabbed dialog assertions and screenshots.
- No report schema, persistence format, provider, or downstream construction-plan contract changes.
