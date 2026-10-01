# GUI Full Inspection Design

## Goal

Make the static damage-recognition GUI use the same full-inspection inference semantics as the CLI: retain all detections, merge overlapping masks only within the same class, render every resulting finding, and list every finding in the GUI table.

## Architecture

The runtime owns inspection aggregation. A public `infer_inspection(image)` method wraps the existing selected-target `infer(image)` result, aggregates its raw instances using the configured same-class mask IoU threshold, and replaces the inspection-facing `damage_findings` with the aggregated findings while preserving the selected target fields for compatibility.

Both `process_path()` and `RecognitionWorker` call this public inspection method. Tracking code continues to call `infer_instances()` and therefore retains single-target tracking behavior.

## GUI Data Flow

1. `RecognitionWorker` reads one image.
2. `YoloSegmentationRuntime.infer_inspection()` returns selected-target compatibility fields plus all aggregated inspection instances.
3. The runtime renderer draws every inspection instance.
4. The measurement renderer annotates every inspection box.
5. The worker stores all aggregated JSON-safe findings in `batch_summary.json`.
6. `DamageWorkflowWindow.on_image_done()` inserts one result-table row per finding; failed and no-detection images still insert one status row.
7. Reports, repair plans, filters, and HUD counts consume the full `damage_findings` list without additional changes.

## Merge Rules

- Merge only identical `class_id` values.
- Require binary-mask IoU greater than or equal to the configured threshold.
- Default threshold remains `0.05`.
- Preserve disjoint same-class findings and all different-class findings.
- Union merged masks, recompute the bounding box and measurements, keep the maximum score, and retain member indices.

## Compatibility

- Existing `infer()` callers continue receiving the highest-score selected target.
- Video tracking continues using `infer_instances()`.
- Static CLI and GUI use `infer_inspection()`.
- Existing output paths and summary structure remain valid; inspection summaries gain complete aggregated findings.

## Testing

- Runtime test: `infer_inspection()` returns merged full findings but `infer()` remains selected-target compatible.
- GUI worker contract test: worker source calls `infer_inspection()` rather than `infer()`.
- GUI table test: one image containing multiple findings creates multiple rows.
- Existing runtime, GUI contract, service, and full project test suites remain green.
