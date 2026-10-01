# Module Test Launchers

## ADDED Requirements

### Requirement: Independent launcher per existing pipeline module

The project SHALL provide an independently executable launcher under `knowledge_pipeline/test/` for each existing pipeline entry point covered by this change. Each launcher MUST call the corresponding existing module entry point and MUST NOT reimplement that module's processing logic.

#### Scenario: Launch a deterministic module test

- **WHEN** a user starts a launcher from the project root or by absolute path
- **THEN** the launcher adds the project root to its import path, invokes the existing module entry point with its hard-coded arguments, and exits with the module's success or failure status

### Requirement: Isolated hard-coded test paths

Each launcher SHALL declare a hard-coded, read-only input path and a hard-coded output path below `knowledge_pipeline/test/results/` (or a child directory), creating only the isolated output directories it needs. No launcher SHALL write to the active production knowledge base or production manifest.

#### Scenario: Run PDF conversion

- **WHEN** the PDF conversion launcher runs
- **THEN** it reads the repository's fixed sample PDF, writes a conversion JSON to the test results directory, and leaves the source PDF unchanged

### Requirement: Observable execution summary

Each launcher SHALL print the module name, input/output paths, elapsed time, output existence/size, and a module-specific summary when the output is readable. Failures SHALL include the exception type and message and return a non-zero process status.

#### Scenario: Inspect a successful output

- **WHEN** a launcher completes successfully
- **THEN** a user can identify the generated artifact and basic counts such as page count, chunk count, retrieval count, or generation evidence count from stdout

### Requirement: End-to-end deterministic RAG smoke path

The project SHALL provide a launcher that invokes the existing modules in dependency order from PDF conversion through generation-context creation using only test-copy artifacts.

#### Scenario: Run the full smoke path without semantic model weights

- **WHEN** the end-to-end launcher runs in an environment without a local embedding model
- **THEN** it still validates the deterministic PDF, chunk, index, lexical retrieval, rerank, and generation stages without claiming semantic retrieval success
