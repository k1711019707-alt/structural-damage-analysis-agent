## ADDED Requirements

### Requirement: Each report and construction request has a hard size bound
The system SHALL generate damage reports and construction plans from one bounded remote draft per finding, with schema-enforced maximum string lengths, list lengths and list-item lengths.

#### Scenario: Five confirmed findings
- **WHEN** a confirmed report contains five findings
- **THEN** each generation stage sends five ordered finding requests, none of which grows with the total finding count

#### Scenario: One confirmed finding
- **WHEN** a confirmed report contains one finding
- **THEN** each generation stage sends one remote request through its validated assembly path

#### Scenario: Arbitrarily many confirmed findings
- **WHEN** the confirmed finding count increases
- **THEN** the system increases the number of bounded requests without increasing the maximum size of any individual response

### Requirement: Atomic drafts are strictly validated and merged
The system SHALL validate the schema, bounds, identity and knowledge citations of every report or construction finding draft before deterministically merging all drafts in source order.

#### Scenario: All single-finding drafts are valid
- **WHEN** every finding draft passes strict validation
- **THEN** the final report contains one AI-authored assessment per finding and the final plan contains one AI-authored method per finding in the original order

#### Scenario: A draft changes its identity
- **WHEN** any report or construction draft changes, omits or adds an image identity
- **THEN** the system rejects remote assembly and does not attach an assessment or method to another finding

### Requirement: Remote report and construction output remains concise and complete
The system SHALL instruct the model to avoid repeated narration and produce concise, non-duplicated engineering content while retaining all required evidence, method, quality, safety and acceptance fields.

#### Scenario: Remote gateway has a practical response limit
- **WHEN** the configured gateway would truncate the former combined response
- **THEN** every schema-bounded atomic response completes independently and the system does not enter local fallback due to total-plan length

### Requirement: Fallback represents a real failed atomic generation
The system SHALL preserve the existing truthful and redacted fallback categories when a request or strict validation still fails after bounded atomic generation.

#### Scenario: A single-finding response is truncated
- **WHEN** strict validation reports EOF for an atomic draft
- **THEN** the final fallback is marked `incomplete_structured_output` and is not represented as AI-authored
