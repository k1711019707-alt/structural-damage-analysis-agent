## ADDED Requirements

### Requirement: Confirmed hold plans may produce non-release previews
The GUI SHALL allow repair-render preview generation for a construction plan whose `review_status` is `confirmed_by_engineer`, whose `plan_status` is either `pending_engineer_review` or `hold`, and whose `construction_released` is `false`.

#### Scenario: Checked render continues after confirmed hold review
- **WHEN** an engineer confirms a `hold` construction plan while “生成修复后渲染图” is checked
- **THEN** the GUI starts the repair-render stage instead of marking it skipped

#### Scenario: Unchecked render remains optional
- **WHEN** an engineer confirms a render-eligible plan while “生成修复后渲染图” is not checked
- **THEN** the GUI completes the workflow without generating repair-render previews

### Requirement: Preview generation does not release construction
Repair-render preview generation SHALL NOT change `construction_released` or describe a `hold` plan as approved for construction.

#### Scenario: Hold preview retains safety boundary
- **WHEN** repair-render previews are generated from a confirmed `hold` plan
- **THEN** the plan remains `hold`, `construction_released` remains `false`, and the GUI describes the render as a non-release preview

### Requirement: Evidence-inconsistent plans remain blocked
The GUI SHALL refuse repair-render generation when `plan_status` is `evidence_inconsistent`, even if the plan review record is confirmed and the render checkbox is checked.

#### Scenario: Inconsistent evidence blocks rendering
- **WHEN** a confirmed plan has `plan_status=evidence_inconsistent`
- **THEN** the GUI does not start the renderer and reports that inconsistent evidence blocks the preview
