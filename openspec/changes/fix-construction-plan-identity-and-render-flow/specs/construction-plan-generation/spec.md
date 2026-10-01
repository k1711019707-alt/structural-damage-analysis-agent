## MODIFIED Requirements

### Requirement: Construction-plan requests are identity-scoped

The construction-plan generator MUST send each atomic request only the validated report subset and deterministic repair lines for that request. It MUST preserve the configured business prompt and selected knowledge references without sending a complete-batch evidence prompt that can introduce unrelated work-item identities.

#### Scenario: Five-item plan is generated one finding at a time

- **WHEN** a confirmed report contains five repair lines
- **THEN** the generator sends five atomic requests
- **AND** each request contains exactly one requested `image_name#finding_index` identity
- **AND** the request instructions do not contain the complete batch evidence prompt
- **AND** the locally merged plan preserves the original report order.

#### Scenario: Provider returns an unrelated identity

- **WHEN** an atomic response contains an `image_name#finding_index` that is not the requested identity
- **THEN** strict local identity validation rejects the response
- **AND** the complete plan uses the existing auditable local fallback
- **AND** the rejected response is never used as a construction work item.

### Requirement: Rendering remains gated by authored-plan provenance

The GUI MUST allow repair-preview rendering only after engineer confirmation, a non-released plan disposition, and valid remote-authored work items. A local-fallback work item MUST keep rendering disabled until a valid remote construction plan is regenerated and reviewed.

#### Scenario: Confirmed remote hold plan

- **WHEN** a plan is `confirmed_by_engineer`, has `plan_status=hold`, has `construction_released=false`, and all work items are remote-authored
- **THEN** the GUI MAY generate a non-construction-release repair preview
- **AND** the status text MUST continue to state that the plan remains paused and is not construction authorization.

#### Scenario: Confirmed local fallback plan

- **WHEN** a plan is confirmed but any work item has `repair_method_source=local_fallback`
- **THEN** the GUI MUST keep repair rendering disabled
- **AND** the status must identify that the remote draft failed validation and must be regenerated.
