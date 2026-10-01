## ADDED Requirements

### Requirement: Detection result columns use stable proportions
The GUI SHALL allocate the detection result table's visible width using the proportions 9% for “编号”, 21% for “文件名”, 26% for “损伤类型”, 14% for “置信度”, 16% for “损伤等级”, and 14% for “状态”.

#### Scenario: Empty table uses balanced columns
- **WHEN** the detection result table is visible without data rows
- **THEN** its six columns follow the configured proportions instead of assigning all residual width to “损伤类型”

#### Scenario: Data does not change proportions
- **WHEN** detection rows with short or long values are inserted
- **THEN** the column proportions remain stable and do not resize from cell contents

### Requirement: Column proportions respond to viewport changes
The GUI SHALL recompute the six column widths whenever the detection result table viewport changes size.

#### Scenario: Window is resized
- **WHEN** the user resizes the main window between supported viewport sizes
- **THEN** all six columns remain visible, their widths sum to the available table viewport, and their proportions stay within rounding tolerance

### Requirement: Existing detection-table behavior is preserved
The proportional layout SHALL preserve the resulting column order, screening-level tooltip, type filtering, continuous display numbering, and CSV export contract.

#### Scenario: Layout changes only presentation
- **WHEN** proportional sizing is applied
- **THEN** detection values, filtering, numbering and exported CSV values remain unchanged
