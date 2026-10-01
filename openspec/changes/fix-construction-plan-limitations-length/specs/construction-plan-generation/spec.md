## MODIFIED Requirements

### Requirement: Draft list entries preserve complete explanatory text

The construction-plan draft schema MUST accept non-empty explanatory strings in list fields without imposing a 140-character per-item limit. It MUST continue to enforce the configured maximum number of entries for each list and all existing scalar-field, identity, evidence, provenance, and review validations.

#### Scenario: Long limitation from a valid remote draft

- **WHEN** a remote construction-plan draft contains a non-empty `limitations` item longer than 140 characters but within the existing list count bound
- **THEN** local schema validation MUST accept the draft
- **AND** plan assembly MUST preserve the full limitation text
- **AND** the plan MUST remain pending engineer review and unreleased until the existing review gate is satisfied.

#### Scenario: Empty list item

- **WHEN** a remote draft contains an empty or whitespace-only list item
- **THEN** local schema validation MUST continue to reject the draft.

#### Scenario: Too many list entries

- **WHEN** a remote draft exceeds the configured maximum number of entries for a list field
- **THEN** local schema validation MUST continue to reject the draft.
