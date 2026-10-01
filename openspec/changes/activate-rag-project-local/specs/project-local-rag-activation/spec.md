## ADDED Requirements

### Requirement: Project-local active manifest precedence

The runtime SHALL prefer `knowledge_base/active_rag.json` under the current project resource root when that manifest exists and is readable, and SHALL fall back to the user-data manifest only when the project-local manifest is absent or unreadable.

#### Scenario: Project manifest is selected

- **WHEN** both the project-local and user-data manifests exist
- **THEN** active status and retrieval SHALL use the project-local manifest and report its path as the active manifest source

#### Scenario: Legacy installation remains compatible

- **WHEN** the project-local manifest does not exist but the user-data manifest exists
- **THEN** active status SHALL continue to use the user-data manifest without requiring migration

### Requirement: Manifest-relative database resolution

The runtime SHALL resolve a relative `database_path` and optional semantic sidecar paths relative to the selected manifest's knowledge-base directory, SHALL reject path traversal outside that directory, and SHALL preserve support for validated absolute paths.

#### Scenario: Project-relative database is healthy

- **WHEN** the selected project manifest points to `production-rag-<version>/knowledge_base_v2.sqlite3`
- **THEN** the runtime SHALL open that project-local database and validate its recorded SHA and schema

#### Scenario: Escaping path is rejected

- **WHEN** a manifest contains a relative path resolving outside the selected knowledge-base directory
- **THEN** the runtime SHALL mark the active version unhealthy and SHALL not open the escaped file

### Requirement: Safe active-version migration

The migration SHALL copy the current active version into a versioned project directory before switching the active manifest, SHALL verify copied file hashes, and SHALL retain the original user-data version.

#### Scenario: Copy and switch succeed

- **WHEN** every required copied file matches its source size and SHA-256
- **THEN** the project manifest SHALL be written atomically and active status SHALL report the project-local version as ready

#### Scenario: Copy validation fails

- **WHEN** any copied required file differs from its source
- **THEN** the project manifest SHALL not be activated and the original user-data manifest SHALL remain usable

### Requirement: Source-aware diagnostics

The active status response SHALL expose the selected manifest path and whether storage is project-local or user-data while preserving existing health, schema, integrity, metadata, and legacy-fallback fields.

#### Scenario: Project-local status is inspected

- **WHEN** the project-local version is active and healthy
- **THEN** status SHALL identify `storage_scope` as `project` and include the selected manifest and database paths
