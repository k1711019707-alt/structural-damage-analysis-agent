## ADDED Requirements

### Requirement: Detection results display screening severity
The GUI SHALL display a dedicated “损伤等级” column for every detection finding and SHALL derive its value only from `damage_findings[].screening_severity.level`.

#### Scenario: Known screening level is displayed
- **WHEN** a detection finding contains a recognized `screening_severity.level`
- **THEN** the GUI displays the corresponding localized level in that finding's “损伤等级” cell

#### Scenario: Missing screening level is conservative
- **WHEN** a detection finding omits `screening_severity.level` or contains an unsupported value
- **THEN** the GUI displays “待判定” and does not infer a level from damage type or confidence

### Requirement: Screening severity labels are localized consistently
The GUI SHALL map `low` to “轻微”, `medium` to “中等”, `high` to “严重”, and `undetermined` to “待判定”, accepting values without case sensitivity.

#### Scenario: Runtime levels use stable Chinese labels
- **WHEN** findings contain the four supported runtime levels
- **THEN** their table cells display “轻微”, “中等”, “严重”, and “待判定” respectively

### Requirement: Placeholder result rows do not claim a damage level
The GUI SHALL display “—” in the “损伤等级” column for no-detection and processing-failure placeholder rows.

#### Scenario: No detection row has no level
- **WHEN** an image completes without any damage findings
- **THEN** its placeholder row displays “—” in the “损伤等级” column

#### Scenario: Failed row has no level
- **WHEN** image processing fails without producing damage findings
- **THEN** its placeholder row displays “—” in the “损伤等级” column

### Requirement: Export preserves displayed screening severity
The detection-result CSV export SHALL include the “损伤等级” column in the same order and with the same displayed labels as the GUI table.

#### Scenario: Export includes localized level
- **WHEN** the user exports a table containing a finding with `screening_severity.level` equal to `high`
- **THEN** the CSV header includes “损伤等级” and the corresponding row contains “严重” in that column

### Requirement: Existing result-table interactions remain compatible
The GUI SHALL continue to filter rows by the “损伤类型” column after adding the severity column, and SHALL expose the severity meaning as image-area screening rather than a structural-safety conclusion.

#### Scenario: Damage type filter still works
- **WHEN** the user selects one available damage type in the result filter
- **THEN** only rows of that damage type remain visible regardless of their screening level

#### Scenario: Severity semantics are disclosed
- **WHEN** the user inspects the “损伤等级” header or a populated severity cell
- **THEN** the GUI provides a tooltip stating that the value is an image-area screening level and not a structural-safety assessment
