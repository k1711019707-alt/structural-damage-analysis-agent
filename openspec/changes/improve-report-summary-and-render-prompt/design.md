## Context

`ResponsesReportService._generate_chunked_report()` generates one validated `ReportFinding` per request, then locally computes the overall level. Its current summary and rationale are fixed strings and do not use the validated findings. The report review dialog faithfully displays those fields, so the UI is not the source of the missing detail.

The repair render worker passes `self.settings.render_prompt` into the selected renderer, and both providers append the reviewed repair method. The FHL adapter uses the project-owned `/images/edits` route through the bundled Node runner; SiliconFlow sends a base64 image-edit request and downloads the returned image. This contract must remain evidence-first and must not imply that a generated image is a construction result.

## Decisions

### Deterministic multi-finding summary

After finding identities and levels are validated, group findings by damage type and normalized level. The summary will state the analyzed count, damage-type distribution, level distribution, highest-level findings, and the required engineering-review boundary. It will not invent dimensions, causes, safety grades, quantities, or completion claims.

### Explicit overall-level rationale

Use the existing `SEVERITY_ORDER` rule. The rationale will state that the overall auxiliary level is the maximum normalized finding level, identify the finding(s) establishing that maximum, explain that the value is visual assistance rather than a structural-safety determination, and require confirmation against site evidence.

### Preserve provider-authored single-finding behavior

When a report contains one finding, retain the provider-authored `executive_summary` and `overall_level_reason`. The richer deterministic assembly applies to the multi-finding merge path where the current implementation discards the per-finding report-level fields.

### Render prompt boundary

The configured prompt remains the provider instruction, and the reviewed method is appended as a separate request fact. Tests must inspect the actual FHL runner payload and SiliconFlow request body, while treating returned images as previews only. No prompt change may authorize adding objects, changing geometry, hiding deformation, or claiming construction completion.

## Verification

1. Generate a five-finding fake remote report and assert the summary includes counts/types/levels and the rationale names the worst-level evidence and review boundary.
2. Keep single-finding summary compatibility tests.
3. Assert FHL and SiliconFlow requests include the configured prompt and reviewed method, preserve the source image, and use the expected provider route.
4. Run focused tests, full suite, syntax compilation, and strict OpenSpec validation.
