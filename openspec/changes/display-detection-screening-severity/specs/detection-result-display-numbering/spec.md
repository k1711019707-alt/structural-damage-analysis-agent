## ADDED Requirements

### Requirement: Detection result rows use continuous display numbers
The GUI SHALL number detection result rows consecutively from 1 across the whole table, independent of each finding's image-local zero-based `index`.

#### Scenario: Multiple images each contain finding zero
- **WHEN** multiple images each produce one finding whose internal `index` is `0`
- **THEN** the visible table numbers those rows `1`, `2`, and so on in insertion order

#### Scenario: One image contains multiple findings
- **WHEN** one image produces multiple findings with any internal indices
- **THEN** each inserted table row receives the next consecutive positive display number

### Requirement: Evidence identity remains unchanged
The GUI SHALL NOT modify the finding's internal `index` or the `(image_name, finding_index)` evidence identity when assigning display numbers.

#### Scenario: Table numbering is presentation-only
- **WHEN** a finding is added to the detection results table
- **THEN** only the table's “编号” cell uses the display sequence and the underlying finding data remains unchanged

### Requirement: Export uses visible display numbers
The detection-result CSV SHALL export the same positive continuous numbers shown in the GUI table.

#### Scenario: Export after multiple image results
- **WHEN** the table displays rows numbered `1` through `N`
- **THEN** the CSV “编号” column contains the same values in the same order
