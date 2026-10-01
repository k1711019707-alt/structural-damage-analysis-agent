## ADDED Requirements

### Requirement: Validate emitted chunks
The chunk stage MUST return machine-readable validation diagnostics covering empty text,
missing provenance, duplicate content, excessive size, missing table headers, and structural metadata.

#### Scenario: Valid parent and child output
- **WHEN** chunks contain text, source markers, page/block provenance, and valid parent links
- **THEN** validation MUST report no error for those checks and include aggregate counts

#### Scenario: Broken table child
- **WHEN** a table child lacks a header or table ID
- **THEN** validation MUST report a type-specific error or warning identifying the chunk ID

### Requirement: Version the new output
The upgraded chunk stage MUST identify its output as `knowledge-chunks.v2` and stage version `chunk.v2`.

#### Scenario: Successful v2 chunking
- **WHEN** chunking completes
- **THEN** the payload schema and stage status MUST contain the v2 markers
