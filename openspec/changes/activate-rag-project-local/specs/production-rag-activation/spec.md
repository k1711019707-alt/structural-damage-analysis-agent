## MODIFIED Requirements

### Requirement: Atomic production RAG activation

Production RAG activation SHALL write the validated active manifest to the selected active-manifest target, where the project-local target takes precedence when it already exists and the user-data target remains the compatibility fallback. Activation SHALL continue to require matching validation evidence, database SHA, schema, integrity, metadata, and scope bindings.

#### Scenario: Existing project-local target is activated

- **WHEN** a validated candidate is activated while a project-local active manifest exists
- **THEN** the new manifest SHALL replace the project-local manifest atomically and SHALL not modify the legacy user-data manifest

#### Scenario: Legacy target is used before migration

- **WHEN** no project-local active manifest exists
- **THEN** activation SHALL continue to write the user-data active manifest using the existing validation gates

#### Scenario: Validation fails

- **WHEN** any required activation gate fails
- **THEN** neither active manifest target SHALL be replaced and the previously active version SHALL remain available
