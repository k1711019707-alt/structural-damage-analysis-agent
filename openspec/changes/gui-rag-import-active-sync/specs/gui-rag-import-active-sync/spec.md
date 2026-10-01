## ADDED Requirements

### Requirement: GUI import activates a verified production candidate
The GUI SHALL start a background versioned production RAG build after a supported document import completes, SHALL run strict validation before activation, and SHALL atomically activate only a verified candidate.

#### Scenario: Import succeeds and candidate is healthy
- **WHEN** a PDF/DOC/DOCX import finishes successfully
- **THEN** the GUI builds a versioned v2 candidate, validates integrity/schema/metadata/scope, atomically updates the project active manifest, and refreshes the active document list

#### Scenario: Candidate build or validation fails
- **WHEN** conversion, indexing, validation, or activation fails
- **THEN** the previous active manifest and database remain unchanged, the GUI reports the failure reason, and no failed candidate is marked active

### Requirement: Folder selection owns profile document scope
The GUI SHALL resolve selected folders to current managed document IDs and SHALL replace stale profile document IDs when folder selection changes.

#### Scenario: Folder selection changes
- **WHEN** a user saves a profile with one or more top-level folders selected
- **THEN** the profile document ID list is replaced by the recursively resolved document IDs from those folders, filtered to documents present in active v2

#### Scenario: Folder selection is empty
- **WHEN** a user saves a profile with no folders selected
- **THEN** stale explicit profile document IDs are cleared and runtime falls back to the configured global active document scope

### Requirement: GUI and active RAG expose one effective state
The GUI SHALL display the selected active manifest, active storage scope, and effective document IDs after synchronization.

#### Scenario: Active state refresh
- **WHEN** activation completes or settings are reopened
- **THEN** the GUI reloads active status and does not present legacy-only documents as production-active documents

### Requirement: Settings save does not cancel background import
The GUI SHALL keep the settings `保存` action available while knowledge-base import or production RAG synchronization is running. Accepting the settings dialog SHALL persist the current settings and SHALL NOT stop, wait for, or invalidate the background import/build/activation workers.

#### Scenario: Save while document import is running
- **WHEN** the user clicks `保存` while a document is still being imported
- **THEN** the settings dialog closes promptly, the import worker continues in the main-window background, and successfully imported documents remain available for the subsequent production RAG synchronization.

#### Scenario: Save while production RAG is synchronizing
- **WHEN** the user clicks `保存` while a verified production candidate is being built or activated
- **THEN** the current settings are saved immediately, the synchronization worker continues, and completion or failure updates the active state without requiring the settings dialog to remain open.

#### Scenario: Background completion after dialog close
- **WHEN** import or synchronization completes after the settings dialog has closed
- **THEN** the GUI SHALL update persisted settings and main-window state, SHALL preserve the previous active manifest on failure, and SHALL NOT access destroyed dialog controls.

### Requirement: Catalog mutations are synchronized without stale scope failures
Each production candidate SHALL be validated against an immutable snapshot of the same managed catalog state that selected its source documents. Catalog mutations arriving while a synchronization worker is running SHALL coalesce into a subsequent synchronization of the latest state.

#### Scenario: Delete while a synchronization is running
- **WHEN** the user deletes a knowledge-base folder or document while a production synchronization is already running
- **THEN** the current candidate SHALL finish against its catalog snapshot and the GUI SHALL automatically run one follow-up synchronization for the latest catalog state instead of discarding the deletion or reporting a mixed-state scope failure.

#### Scenario: Delete leaves remaining documents
- **WHEN** folder deletion leaves one or more ready managed documents
- **THEN** the follow-up candidate SHALL contain exactly those remaining documents and strict scope validation SHALL not require any deleted document.

### Requirement: Empty managed catalog explicitly disables RAG
When a confirmed folder or document deletion leaves no ready managed documents, the GUI SHALL atomically activate an explicit disabled knowledge-base state with an empty effective scope. Runtime retrieval SHALL fail closed and SHALL NOT silently use the previous active corpus or another legacy manifest.

#### Scenario: Delete the final folder
- **WHEN** deletion removes the final ready knowledge-base document
- **THEN** no empty database candidate is built, the active manifest records `catalog_empty`, settings/profile document IDs are cleared, and the GUI reports that the production knowledge base has been disabled successfully rather than showing a candidate-validation failure.
