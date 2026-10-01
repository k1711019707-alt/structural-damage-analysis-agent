## ADDED Requirements

### Requirement: GUI production candidates include a semantic sidecar
The GUI production rebuild SHALL generate a normalized local semantic sidecar and companion manifest from the exact retrieval children of the candidate v2 SQLite database before activation.

#### Scenario: Semantic candidate succeeds
- **WHEN** the GUI catalog contains ready documents and the local embedding model can encode the candidate retrieval corpus
- **THEN** the candidate contains a semantic index and manifest whose count, ordering, content hashes, and corpus fingerprint match the candidate SQLite

#### Scenario: Semantic candidate fails
- **WHEN** the embedding model, generated arrays, manifest, or corpus identity is unavailable or invalid
- **THEN** synchronization reports an actionable failure and leaves the previous active RAG unchanged

### Requirement: Activation binds semantic integrity
The activation manifest MUST record the semantic index and semantic manifest paths and SHA-256 digests, and runtime health SHALL reject replacement, unsafe arrays, incompatible schema, or corpus mismatch.

#### Scenario: Valid semantic activation
- **WHEN** database, semantic artifacts, query expectations, and scope expectations pass strict replayable validation
- **THEN** activation atomically selects the candidate and runtime reports semantic configured and healthy

#### Scenario: Semantic artifact changes after validation
- **WHEN** either semantic artifact changes before or after validation replay
- **THEN** activation fails closed and does not replace the active manifest

### Requirement: GUI exposes effective semantic status
The knowledge-base GUI SHALL display whether the selected active RAG has a healthy semantic sidecar and SHALL distinguish a production-active semantic corpus from lexical-only historical artifacts.

#### Scenario: Active semantic corpus is displayed
- **WHEN** the settings view reads a healthy active manifest with semantic metadata
- **THEN** it displays semantic enabled status, model identity, and indexed retrieval-child count without exposing credentials

