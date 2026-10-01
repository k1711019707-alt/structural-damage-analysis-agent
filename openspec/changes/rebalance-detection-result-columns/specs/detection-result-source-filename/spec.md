## ADDED Requirements

### Requirement: Detection results display the source filename
The GUI SHALL display a dedicated “文件名” column immediately after “编号” for every detection result row.

#### Scenario: Damage finding displays its image name
- **WHEN** an image produces one or more damage findings
- **THEN** every inserted finding row displays that source image's filename

#### Scenario: Placeholder row displays its image name
- **WHEN** an image produces no findings or fails processing
- **THEN** the inserted placeholder row still displays that source image's filename

### Requirement: Full filename remains accessible
The GUI SHALL provide the full source filename as a tooltip on each populated filename cell.

#### Scenario: Long filename is visually elided
- **WHEN** the visible filename text does not fit within its proportional column
- **THEN** hovering the filename cell exposes the complete filename

### Requirement: Filename participates in export without changing type filtering
The CSV export SHALL include “文件名” after “编号”, while the GUI type filter SHALL continue to filter using the “损伤类型” column.

#### Scenario: Export and filter remain aligned
- **WHEN** result rows are filtered or exported after the filename column is added
- **THEN** filtering uses damage type and exported rows include the corresponding source filename
