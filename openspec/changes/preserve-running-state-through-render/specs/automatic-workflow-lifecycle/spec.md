## ADDED Requirements

### Requirement: Primary action reflects the complete automatic workflow
The GUI SHALL display the primary action as `停止` from the start of recognition through report review, construction-plan generation, construction-plan review, and selected repair rendering. The GUI SHALL restore `启动` only after the workflow completes, is cancelled, or fails.

#### Scenario: Report waits for human review
- **WHEN** report generation finishes and the report is awaiting human confirmation
- **THEN** the primary action remains a disabled `停止` action

#### Scenario: Construction plan waits for engineer review
- **WHEN** construction-plan generation finishes and the plan is awaiting engineer confirmation
- **THEN** the primary action remains a disabled `停止` action

#### Scenario: Workflow reaches a terminal state
- **WHEN** the selected workflow completes, is cancelled, or fails
- **THEN** the primary action returns to an enabled `启动` action

### Requirement: Reviewed repair rendering does not block the GUI
The application SHALL execute selected repair rendering in a background worker after an eligible construction plan is confirmed and MUST keep the Qt GUI event loop responsive while rendering is in progress.

#### Scenario: Eligible checked plan is confirmed
- **WHEN** an engineer confirms a render-eligible plan and `生成修复渲染图` is checked
- **THEN** the application enters the repair-rendering stage, starts a background render worker, and keeps the primary action as `停止`

#### Scenario: GUI event arrives during rendering
- **WHEN** a render item is waiting on an external process or network response
- **THEN** queued GUI events are processed without waiting for that render item to finish

#### Scenario: Render worker reports an item
- **WHEN** the background worker finishes or cancels one render item
- **THEN** the GUI adds that result to the render result list on the GUI thread

### Requirement: Repair rendering supports stopping remaining items
The application SHALL allow the enabled primary `停止` action to request cancellation during background repair rendering and SHALL prevent unstarted render items from beginning after the current item returns.

#### Scenario: User stops active rendering
- **WHEN** the user activates `停止` while the repair render worker is running
- **THEN** the worker records remaining items as cancelled, the button remains a disabled `停止` while shutdown is pending, and the workflow returns to `启动` after cancellation finishes

### Requirement: Every repair-render exit reaches a visible terminal state
The GUI SHALL restore the idle primary action and present a terminal status when rendering completes, fails, is cancelled, has no eligible items, is not selected, or is blocked by engineering disposition.

#### Scenario: Rendering completes with mixed item results
- **WHEN** the background worker returns successful and failed item results
- **THEN** the GUI displays all results, marks the workflow complete, and restores `启动`

#### Scenario: No item qualifies for rendering
- **WHEN** the repair selection contains no renderable items
- **THEN** the GUI records that the stage was skipped, marks the workflow complete, and restores `启动`

#### Scenario: Optional rendering is not selected
- **WHEN** construction review is confirmed and the render option is unchecked
- **THEN** the GUI marks the workflow complete and restores `启动`

#### Scenario: Engineering disposition blocks rendering
- **WHEN** construction review is confirmed but the plan is not render-eligible
- **THEN** the GUI reports the blocking disposition, marks the workflow complete, and restores `启动`

### Requirement: Repair-render option uses its complete label
The repair-render option SHALL display `生成修复渲染图` at every supported window size, and its tooltip SHALL use the same wording.

#### Scenario: Compact workbench layout
- **WHEN** the workbench is displayed at the supported compact viewport
- **THEN** the option displays the complete `生成修复渲染图` text without changing to an abbreviated label
