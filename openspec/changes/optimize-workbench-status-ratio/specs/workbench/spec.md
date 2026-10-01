## MODIFIED Requirements

### Requirement: Bottom status row proportions

The workbench bottom status row MUST allocate horizontal space to the status, plan preview, event log, and actions panels with stretch factors `2:4:5:3`.

#### Scenario: Wide viewport

- **GIVEN** the workbench is displayed at 1680×980 or 1440×980
- **WHEN** Qt lays out the bottom status row
- **THEN** all four panels are visible, the plan preview keeps at least 300 px at the wide viewport, and the event log remains wider than the preview for log readability
- **AND** the event log is less than twice the preview width

#### Scenario: Responsive viewport

- **GIVEN** the workbench is displayed at a low-height viewport such as 1280×720
- **WHEN** the row exceeds the available vertical viewport
- **THEN** the existing workbench scroll area remains responsible for reaching the actions panel
- **AND** no panel receives a new fixed width that would force horizontal overflow.
