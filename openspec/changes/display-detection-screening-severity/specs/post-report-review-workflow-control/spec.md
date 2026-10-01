## ADDED Requirements

### Requirement: Report confirmation preserves the running control state
The GUI SHALL show the primary workflow button with “停止”, the stop icon, and the running-state style from the moment a damage report is confirmed until construction-plan generation leaves its active stage.

#### Scenario: Confirmed report schedules construction plan
- **WHEN** the user confirms a damage report and the GUI schedules construction-plan generation
- **THEN** the primary workflow button displays “停止” before the scheduled plan worker starts

#### Scenario: Construction plan generation starts directly
- **WHEN** `build_plan()` starts construction-plan preparation from a confirmed report
- **THEN** the primary workflow button displays “停止” with the running-state style and remains disabled under the existing non-cancellable plan-worker contract

### Requirement: Terminal plan states restore existing controls
The GUI SHALL retain its existing terminal handling after construction-plan generation succeeds, fails, or stops at an engineer-review gate.

#### Scenario: Plan generation reaches a terminal handler
- **WHEN** construction-plan generation invokes its existing success or failure handler
- **THEN** the handler controls whether the button returns to “启动” without being overridden by the report-confirmation transition
