## MODIFIED Requirements

### Requirement: Multi-finding report review contains an evidence-grounded summary

After validated multi-finding AI responses are merged, the report MUST summarize the number of findings, damage-type distribution, normalized level distribution, highest-level finding(s), and the need for engineer review. The overall-level rationale MUST explain the maximum-level rule and MUST state that the value is visual assistance, not a structural-safety determination.

#### Scenario: Five findings are merged

- **WHEN** five finding responses pass identity and schema validation
- **THEN** the report summary describes the five analyzed findings and their damage types/levels
- **AND** the overall rationale identifies the finding(s) establishing the maximum normalized level
- **AND** the report remains pending human review.

#### Scenario: One finding is generated

- **WHEN** a report contains one finding
- **THEN** the provider-authored summary and rationale remain available
- **AND** the report still retains the visual-assistance and engineer-review boundary.

### Requirement: Repair-render requests preserve evidence-first prompt constraints

When repair rendering is enabled and the plan passes the review gate, the selected image-edit provider MUST receive the configured render prompt and the reviewed repair method together with the original source image. The request MUST remain a preview instruction and MUST prohibit adding unobserved objects, changing geometry, hiding deformation, or claiming construction completion.

#### Scenario: FHL edit request

- **WHEN** an eligible reviewed item is sent to FHL
- **THEN** the project-owned image-edit route receives the original image and composed prompt
- **AND** the prompt contains the configured constraints and reviewed method.

#### Scenario: SiliconFlow edit request

- **WHEN** an eligible reviewed item is sent to SiliconFlow
- **THEN** the image-edit request contains the original image data and composed prompt
- **AND** the returned image is recorded as a preview result, not construction release.
