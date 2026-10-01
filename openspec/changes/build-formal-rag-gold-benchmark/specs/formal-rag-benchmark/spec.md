## ADDED Requirements

### Requirement: Fixed and traceable benchmark composition
The system MUST generate exactly 150 benchmark candidate records from an explicitly identified knowledge-base snapshot, with the configured eight query-type quotas and database/source provenance.

#### Scenario: Generate the formal candidate set
- **WHEN** the generator runs against a readable compatible SQLite benchmark snapshot
- **THEN** it writes exactly 150 records with quotas 25/35/25/20/15/10/10/10 for exact identifier, paraphrase, numeric-unit, table, multi-evidence, scope isolation, no-answer, and risk-negation-exception queries
- **AND** the manifest records the input path, SHA-256, schema observations, generation seed, type counts, document counts, and source identities.

### Requirement: Evidence-grounded positive records
Every answerable automatically generated record MUST reference real indexed retrieval evidence and MUST preserve enough source metadata for page-level review.

#### Scenario: Inspect an answerable record
- **WHEN** an answerable benchmark record is read
- **THEN** it contains scope document IDs, candidate direct chunk IDs, source pages or markers, evidence text, a candidate answer, required facts, and forbidden-error guidance
- **AND** referenced chunk and document IDs exist in the bound SQLite snapshot.

### Requirement: Automatic candidates are not Gold
The system MUST NOT mark automatically generated questions, answers, or retrieval candidates as manually verified Gold truth.

#### Scenario: Write candidate and review assets
- **WHEN** generation completes without completed human PDF review
- **THEN** every record has `annotation_status: needs_human_review` and `gold_label: false`
- **AND** the generated review copy is separate from the immutable automatic candidate JSONL.

### Requirement: Gold publication gate
The system MUST validate human review fields before publishing a Gold dataset.

#### Scenario: Reject incomplete review
- **WHEN** a review record lacks original-PDF page verification, a reviewed answer/abstention decision, relevance labels, reviewer identity, or direct evidence for an answerable query
- **THEN** Gold publication fails with record-specific diagnostics and does not overwrite candidate files.

#### Scenario: Publish reviewed Gold records
- **WHEN** all selected review records satisfy the review schema and all referenced evidence exists
- **THEN** the publisher writes a separate versioned Gold JSONL and manifest with reviewer/review timestamps and source/database hashes.

### Requirement: Structural and distribution validation
The project MUST provide automated validation for record count, type quotas, IDs, evidence references, scope isolation annotations, and no-answer invariants.

#### Scenario: Validate generated candidates
- **WHEN** the candidate validator runs
- **THEN** it confirms unique question IDs, exact quota counts, existing evidence references, empty relevant sets for no-answer cases, and no automatic Gold labels
- **AND** it emits machine-readable and human-readable coverage reports.

### Requirement: Read-only generation
Benchmark generation MUST be read-only with respect to the input SQLite database, source PDFs, active production manifest, and existing benchmark files.

#### Scenario: Regenerate candidates
- **WHEN** the generator runs repeatedly with the same input hash and seed
- **THEN** the semantic record content and ordering are deterministic
- **AND** no input database, PDF, active manifest, or legacy benchmark file is modified.
