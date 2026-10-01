## ADDED Requirements

### Requirement: Multi-item construction drafts avoid oversized single responses
The construction service SHALL split plans with more than three work items into independently schema-validated batches of no more than three items and SHALL provide sufficient bounded output budget for each batch.

#### Scenario: Five findings generate a construction plan
- **WHEN** a confirmed report contains five deterministic repair items
- **THEN** the service requests two construction draft batches, preserves cumulative streaming, and merges all five identities in original order

#### Scenario: A batch returns malformed JSON
- **WHEN** any batch returns truncated or otherwise invalid JSON
- **THEN** the invalid batch is rejected and partial remote text is not assembled into the final construction plan

### Requirement: Construction fallback reporting identifies the actual failure stage
The system SHALL distinguish provider unavailability from incomplete returned JSON, schema-invalid returned JSON, and other generation-processing errors in persisted audit and user-facing fallback summaries.

#### Scenario: Stream completes with an unterminated JSON string
- **WHEN** strict validation reports EOF while parsing streamed construction JSON
- **THEN** the fallback category is `incomplete_structured_output` and the plan states that a remote draft was returned but incomplete

#### Scenario: Provider request cannot complete
- **WHEN** the transport exhausts its existing retry behavior without usable output
- **THEN** the fallback category is `provider_unavailable` and the plan may state that the remote service was unavailable

### Requirement: Reviewed terminal workflows reach 100 percent
The GUI SHALL set overall progress to 100% after construction review when no applicable rendering task remains.

#### Scenario: Non-blocked plan is reviewed without rendering selected
- **WHEN** engineering review is confirmed and optional rendering is not selected
- **THEN** the workflow is marked complete at 100%

#### Scenario: Blocked plan is reviewed
- **WHEN** engineering review is confirmed for a `hold` or `evidence_inconsistent` plan
- **THEN** the workflow is marked complete at 100% while status text continues to identify the engineering block and no rendering starts

#### Scenario: Rendering is selected and allowed
- **WHEN** review is confirmed for a non-blocked plan and rendering is selected
- **THEN** rendering starts under the existing workflow and progress reaches 100% only when rendering completes or is skipped because no eligible image exists

### Requirement: Current-file preview reflects persisted review
The GUI SHALL reload the persisted construction Markdown after draft save or review confirmation so review status, reviewer, review time, and notes do not remain stale.

#### Scenario: Engineer confirms construction review
- **WHEN** the reviewed JSON and Markdown are persisted
- **THEN** the current-file preview displays the reviewed Markdown and no longer shows pending-review or blank-reviewer metadata
