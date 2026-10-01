## MODIFIED Requirements

### Requirement: Enabled rendering attempts every reviewed work item

When repair rendering is enabled and the construction plan is engineer-confirmed, non-released, evidence-consistent, and remote-authored, the system MUST enqueue every reviewed construction work item that has a reviewed repair method. Detection-result success, detection boxes, and exact image-name matching MUST NOT silently remove the work item from the queue.

#### Scenario: Detection metadata is incomplete

- **WHEN** a reviewed work item has a valid original image path and repair method but its corresponding detection result is missing, failed, or has no findings
- **THEN** the work item MUST still be enqueued for rendering.

#### Scenario: Source image is unavailable

- **WHEN** an enqueued work item has no readable source file
- **THEN** the renderer MUST receive the item and return an auditable failed result
- **AND** the GUI MUST report a failed render attempt rather than a skipped render stage.

#### Scenario: Safety gate is not satisfied

- **WHEN** the plan is unreviewed or released
- **THEN** the system MUST keep rendering blocked.

#### Scenario: Confirmed local fallback preview

- **WHEN** the plan is engineer-confirmed, non-released, and contains a local-fallback work item
- **THEN** the system MUST enqueue its reviewed work items when rendering is enabled
- **AND** the GUI MUST label the output as a local conservative preview that does not authorize construction.

#### Scenario: Confirmed evidence-inconsistent preview

- **WHEN** the plan is engineer-confirmed, non-released, and marked `evidence_inconsistent`
- **THEN** the system MUST enqueue its reviewed work items when rendering is enabled
- **AND** the GUI MUST label the output as a pending-review preview and preserve the evidence inconsistency warning.
