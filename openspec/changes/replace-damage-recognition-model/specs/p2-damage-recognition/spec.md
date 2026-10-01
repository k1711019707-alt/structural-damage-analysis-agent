## ADDED Requirements

### Requirement: Project-contained formal P2 model
The system SHALL ship and load exactly one formal damage-recognition model at `models/best.pt`, and all runtime, GUI, packaging, and reproduction paths MUST resolve that project-contained artifact without referencing the external delivery directory.

#### Scenario: Source runtime loads formal model
- **WHEN** the damage-recognition runtime starts from source
- **THEN** it loads the project-local `models/best.pt`
- **AND** the loaded artifact SHA-256 matches the deployment manifest

#### Scenario: Frozen runtime contains formal model
- **WHEN** the desktop application is packaged
- **THEN** the package contains the same `models/best.pt` and model manifest
- **AND** startup validation rejects a missing, truncated, or checksum-mismatched artifact

### Requirement: P2 segmentation contract
The formal model MUST be a YOLO segmentation model with the eight ordered concrete-damage classes and a P2 output stride of 4.

#### Scenario: Formal model contract succeeds
- **WHEN** the project validates the copied formal checkpoint
- **THEN** the model task is `segment`
- **AND** class IDs 0 through 7 map to Concrete crushing, Delamination, Microcrack, Minor spalling, Moderate spalling, Rebar corrosion, Structural crack, and Structural deformation
- **AND** model strides include 4, 8, 16, and 32

#### Scenario: Invalid replacement is rejected
- **WHEN** a checkpoint has the wrong task, class order, class count, or lacks the P2 stride
- **THEN** formal-model validation fails before the checkpoint is accepted for release

### Requirement: Existing detection result compatibility
The P2 model SHALL integrate through the existing runtime and preserve the `damage-finding.v1` result contract used by the GUI, report, and construction-plan flows.

#### Scenario: Real image inference
- **WHEN** the formal model processes a supported image
- **THEN** the runtime returns segmentation instances with class, confidence, box, mask, area, screening severity, model provenance, and inference provenance
- **AND** downstream consumers receive `damage-finding.v1` findings without an adapter to the external delivery directory

### Requirement: Portable P2 training source
The project SHALL contain the P2 architecture and a project-relative training configuration sufficient to load or continue training the formal model without paths from the source delivery machine.

#### Scenario: Training dry run
- **WHEN** the training entry point is executed with `--dry-run`
- **THEN** all model, data, and output paths resolve from the project or workspace
- **AND** no resolved value references `E:\桌面\海之子\检测` or the original developer profile

### Requirement: Legacy model removal after validation
The project SHALL remove the previous formal checkpoint and its checksum metadata only after the new model passes the model contract, runtime inference, GUI dispatch, and packaging checks.

#### Scenario: Completed migration
- **WHEN** all replacement verification tasks pass
- **THEN** `models/best.pt` has the new P2 SHA-256
- **AND** no project runtime or package contains the old formal checkpoint or old checksum
- **AND** no automatic fallback to the old model exists

### Requirement: Non-reentrant GUI workflow updates
The GUI SHALL consume recognition, workflow-stage, and streamed-generation worker signals without recursively pumping the Qt event loop from their slots.

#### Scenario: Queued generation updates remain sequential
- **WHEN** a streamed-generation update queues another GUI update while the current update slot is running
- **THEN** the queued update is not dispatched until the current slot returns to the Qt event loop
- **AND** the complete streamed text remains visible in the generation preview

#### Scenario: Workflow completes without nested event processing
- **WHEN** detection, report generation, and construction-plan generation update GUI state
- **THEN** their GUI slots update status and preview controls without calling `QApplication.processEvents()`
- **AND** normal worker-thread signal delivery remains asynchronous and responsive

#### Scenario: Manual synchronous generation retains visible preview
- **WHEN** a legacy manual report or construction-plan request streams text on the GUI thread
- **THEN** its preview repaints while the request runs without dispatching unrelated queued Qt events
