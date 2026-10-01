## ADDED Requirements

### Requirement: Portable builds use explicit machine-independent inputs
The portable build MUST obtain the FHL script, Node executable, and semantic embedding model from validated build parameters, environment discovery, or project-owned staged resources, and the source-controlled PyInstaller specification MUST NOT contain developer-specific absolute paths.

#### Scenario: Required build input is available
- **WHEN** the build is invoked with valid local FHL, Node, and semantic-model inputs
- **THEN** it stages those inputs below the isolated release staging root and records their relative paths and SHA-256 digests

#### Scenario: Required build input is unavailable
- **WHEN** any required FHL, Node, or semantic-model input cannot be resolved locally
- **THEN** the build fails before invoking PyInstaller and reports which input must be supplied

### Requirement: Portable builds snapshot only healthy active RAG
The portable build SHALL stage the project-selected active RAG manifest and its exact database and semantic artifacts only after strict health and integrity validation.

#### Scenario: Active RAG is healthy and stable
- **WHEN** the active manifest references a healthy database and semantic sidecar with matching digests and no non-empty SQLite WAL
- **THEN** the build copies the selected files into an isolated staging tree and verifies source and destination digests before continuing

#### Scenario: Production RAG is rebuilding or inconsistent
- **WHEN** the database has a non-empty WAL, a referenced artifact is missing, a path escapes the project knowledge root, or any digest changes during staging
- **THEN** staging fails closed without invoking PyInstaller or changing the active manifest

### Requirement: Bundled RAG is portable and privacy-safe
The staged active manifest MUST use portable relative paths and MUST exclude rollback history, machine-specific source paths, credentials, user settings, inactive source documents, historical candidates, and legacy knowledge databases. Every source document selected by the final active manifest MUST be copied under a portable `source_files` path only after SHA-256 verification.

#### Scenario: Active manifest contains local provenance paths
- **WHEN** the source manifest contains absolute source-document, validation-report, rollback, or prior-manifest paths
- **THEN** the staged manifest removes or rewrites those values to bundled relative paths while preserving document names, IDs, hashes, counts, and active artifact integrity

#### Scenario: Active source document is missing or changed
- **WHEN** an active source document is unavailable, has no expected digest, or does not match its recorded SHA-256
- **THEN** staging fails and does not create a distributable candidate

#### Scenario: Inactive documents exist beside the active corpus
- **WHEN** the project knowledge directory contains documents or candidate builds not referenced by the final active manifest
- **THEN** those inactive files are not copied into the portable staging tree

### Requirement: Semantic retrieval works offline in the frozen application
The portable release MUST include the local sentence-transformer snapshot identified by the active semantic manifest and SHALL load it without network access when encoding a query.

#### Scenario: Frozen semantic query is executed offline
- **WHEN** the extracted application loads its bundled semantic sidecar and encodes a query with network access disabled
- **THEN** it loads the packaged model snapshot, produces the expected vector dimension, and retains healthy semantic retrieval status

#### Scenario: Packaged model does not match the active semantic contract
- **WHEN** the model snapshot is absent, unreadable, or produces a different embedding dimension
- **THEN** release verification fails and the candidate is not promoted

### Requirement: Frozen application contains current RAG and assistant modules
The PyInstaller build MUST include the runtime RAG, knowledge-pipeline, semantic retrieval, and read-only assistant modules reachable from the current GUI.

#### Scenario: Extracted candidate starts
- **WHEN** the complete onedir candidate is launched from an unrelated working directory
- **THEN** startup succeeds and the application can import the assistant, inspect bundled RAG health, perform scoped lexical retrieval, and initialize semantic retrieval

### Requirement: Frozen RAG updates use a writable overlay
The frozen application MUST treat the bundled active RAG as a read-only baseline and MUST write rebuilt candidates and activation manifests below the per-user application-data knowledge root.

#### Scenario: First frozen launch has no user override
- **WHEN** the extracted application starts without a user active manifest
- **THEN** it selects and validates the bundled active RAG

#### Scenario: User rebuild activates a new corpus
- **WHEN** a frozen GUI rebuild succeeds and writes a healthy user active manifest
- **THEN** subsequent retrieval selects the user manifest without modifying the bundled baseline

#### Scenario: User override is rolled back
- **WHEN** the current user manifest has no earlier user version to restore
- **THEN** rollback removes the user override and retrieval selects the unchanged bundled baseline

### Requirement: Release manifest proves bundled resources
The build SHALL emit a release manifest containing the product version, entrypoint, relative model/RAG/semantic/FHL resource paths, artifact identities, and SHA-256/size records for every bundled file without credentials or source-machine paths.

#### Scenario: Release manifest is audited
- **WHEN** verification reads the completed `release_manifest.json`
- **THEN** every listed file exists with the recorded size and digest, required RAG/model resources are represented, and no secret or absolute source path is present

### Requirement: Packaging does not interfere with active RAG rebuilds
Release staging and formal build execution MUST be deferred while the GUI background production-RAG rebuild is active.

#### Scenario: Background rebuild is in progress
- **WHEN** the operator identifies an active GUI production-RAG rebuild
- **THEN** implementation and fixture tests may continue but no production RAG staging, validation, PyInstaller build, activation, rollback, or archive generation is executed

### Requirement: Source release matches the portable production baseline
The release workflow SHALL create a separate versioned source archive from the same immutable staged RAG, semantic-model, FHL, Node, and formal-model inputs used by the portable candidate.

#### Scenario: Source release is created
- **WHEN** the portable staging tree has passed integrity and redaction gates
- **THEN** the source candidate contains the application source, locked environment, formal model, staged active RAG and source documents, offline semantic model, FHL runtime, relative-path manifest, and a startup entrypoint

#### Scenario: Source candidate is audited
- **WHEN** source verification enumerates the extracted candidate
- **THEN** every recorded file matches its size and SHA-256, no user API/settings file or developer-machine path is included, and source-mode RAG and semantic retrieval work without consulting the build machine's cache
