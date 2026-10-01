## ADDED Requirements

### Requirement: RAGAS environment and source reuse
The generator MUST run with the configured Conda RAGAS environment and MUST construct RAGAS documents from the project's existing parsed retrieval chunks for the PDFs in the requested test directory.

#### Scenario: Prepare source documents
- **WHEN** the three test PDFs have matching indexed documents and retrieval chunks
- **THEN** the generator creates LangChain documents with document ID, source path, source hash, chunk IDs, pages, headings, and source markers without rerunning PDF OCR.

### Requirement: Stratified RAGAS generation
The generator MUST use the installed RAGAS TestsetGenerator and MUST attempt a documented per-document quota rather than allowing the largest PDF to monopolize all samples.

#### Scenario: Generate the candidate set
- **WHEN** RAGAS generation runs successfully
- **THEN** it produces single-hop and available multi-hop samples across the three source documents and reports target versus actual counts per source.

### Requirement: Non-Gold review boundary
Every automatically generated record MUST remain a review candidate until a human verifies the original PDF, page, evidence text, and answer.

#### Scenario: Write project candidate records
- **WHEN** a RAGAS sample is converted to the project schema
- **THEN** it contains `annotation_status=needs_human_review`, `gold_label=false`, source provenance, reference contexts, and an empty manual review section.

### Requirement: Reproducible and credential-safe output
The result package MUST record source and script hashes, RAGAS and model identities, generation parameters, errors, and rerun commands without recording API credentials.

#### Scenario: Finish generation
- **WHEN** the run completes or partially completes
- **THEN** the output contains raw RAGAS JSONL, project candidates, review template, manifest, coverage report, and failure details, and no credential value is written.
