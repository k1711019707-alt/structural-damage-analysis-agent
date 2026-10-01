## ADDED Requirements

### Requirement: Confirmed v3 report is the only planning fact source
The system SHALL generate repair and construction plans only from a locally validated `damage-report.v3` whose review status, human-review status, reviewer, review time, integrity state, and finding levels satisfy the existing downstream gate. Detection output MAY supply image paths but MUST NOT override report damage types, levels, methods, or quantities.

#### Scenario: Invalid or incomplete report enters planning
- **WHEN** a report is legacy, pending, missing a reviewer or review time, correspondence-invalid, or contains `undetermined`
- **THEN** no repair or construction-plan artifact is generated and the GUI reports the unmet requirement

#### Scenario: Detection and confirmed report disagree on level
- **WHEN** a confirmed report level differs from a legacy detection screening level
- **THEN** the repair plan uses the confirmed report level and does not persist the detection level as an engineering fact

### Requirement: Deterministic repair method cards
The system SHALL map each confirmed finding to a locally owned repair method card identified by `RC-R01`, `RC-C02`, `RC-P01`, `RC-S01`, or `RC-U01`. Each line SHALL preserve `repair_item_id = image_name#finding_index`, method selection basis, site verifications, upgrade conditions, excluded conclusions, stop-work conditions, report basis, and review status.

#### Scenario: Known damage type is planned
- **WHEN** a confirmed finding matches a supported damage type
- **THEN** the corresponding deterministic card is selected and its method identity cannot be changed by the model

#### Scenario: Unknown or high-risk damage is planned
- **WHEN** a damage type is unsupported, the confirmed level is high or critical, or structural deformation requires specialist assessment
- **THEN** the line uses `RC-U01` or `hold`, requires specialist verification, and cannot be presented as an ordinary repair method

### Requirement: Scale-free planning
The system SHALL keep `component_area_ratio` and `physical_area_mm2` null unless independently verified physical measurements are explicitly supplied through an approved local contract. Pixel area, mask ratio, bounding boxes, and inferred crack geometry MUST NOT be used as repair quantities or method-selection facts.

#### Scenario: Only image detection data exists
- **WHEN** a confirmed report has no independently verified physical measurement
- **THEN** plan JSON stores null scale fields and worker-facing output displays that dimensions and quantities require site verification

### Requirement: Model output is an expansion draft only
The remote model SHALL return only allowed descriptive expansion fields for existing deterministic work items. Plan status, construction release, provenance, evidence counts, repair identities, report facts, method IDs, base methods, scale fields, and review facts MUST be constructed or overwritten locally and MUST NOT be model-controlled.

#### Scenario: Model attempts to approve construction
- **WHEN** model output or a profile prompt requests `approved_for_construction` or `construction_released=true`
- **THEN** the generated plan remains locally controlled, pending review or on hold, with `construction_released=false`

#### Scenario: Model changes a deterministic method
- **WHEN** model content attempts to replace a method ID, base method, damage type, level, or report basis
- **THEN** the attempt is rejected or ignored and the persisted facts equal the deterministic repair-plan snapshot

### Requirement: Exact construction-item correspondence
Every remote or assembled construction plan SHALL contain every deterministic repair identity exactly once, contain no additional identity, and preserve deterministic input order. This rule SHALL apply to both single-request and chunked generation.

#### Scenario: Model omits, duplicates, or adds an item
- **WHEN** the returned identity multiset differs from the repair-plan identity list
- **THEN** remote output is rejected and the complete local conservative plan is generated instead

#### Scenario: Chunked output is merged
- **WHEN** more than eight repair lines are generated in batches
- **THEN** every batch forbids additions and the merged result passes a final global identity and order check

### Requirement: Scoped knowledge references
Construction-plan citations SHALL be limited to source markers present in the current `GenerationContext.retrieved_chunks`. Retrieval context and manifests SHALL preserve active-v2 retrieval mode, anchors, context groups, warnings, route diagnostics, source mode, and evidence hash.

#### Scenario: Model cites an unknown source marker
- **WHEN** a work item includes a reference outside the retrieved source-marker set
- **THEN** remote output is rejected and the unknown reference is not persisted as an authoritative citation

#### Scenario: Hierarchical production RAG supplies context
- **WHEN** active-v2 retrieval returns anchors and expanded context groups
- **THEN** the construction prompt and generation manifest retain those fields without invoking a legacy knowledge-base path

### Requirement: Local construction safety state machine
The application SHALL compute evidence consistency, plan status, and release state locally. Evidence inconsistency SHALL set `evidence_inconsistent`; any hold line SHALL set `hold`; otherwise generation SHALL set `pending_engineer_review`. Construction generation SHALL always set `construction_released=false`.

#### Scenario: Evidence counts conflict
- **WHEN** report findings, repair lines, or traced image identities do not correspond
- **THEN** the plan is marked `evidence_inconsistent`, remains unreleased, and states the blocking reason

#### Scenario: Normal plan generation succeeds
- **WHEN** evidence is consistent and no work item is on hold
- **THEN** the plan is `pending_engineer_review` and is not released for construction

### Requirement: Conservative local fallback
If remote generation fails validation, transport, or provider execution, the system SHALL create one local work item per deterministic repair line using the same method card and report basis. It SHALL persist atomic JSON, Traditional-Chinese Markdown, manifest provenance, generation mode, and a credential-redacted failure reason.

#### Scenario: Provider output violates the contract
- **WHEN** strict schema, identity, citation, or safety validation fails
- **THEN** a complete local conservative plan is persisted and visibly identified as a fallback requiring engineer review

### Requirement: Traditional-Chinese worker-facing plan
The system SHALL render a single locally controlled Traditional-Chinese Markdown plan with project basics, evidence summary, repair scope table, per-item method and report basis, site checks, materials and equipment, procedures, figures or explicit placeholders, quality and acceptance, safety and stop-work conditions, references, post-repair records, review status, and limitations. Internal prompts and full retrieval text MUST remain outside the worker-facing document.

#### Scenario: Optional image paths are absent
- **WHEN** original, annotated, or approved construction figures are unavailable
- **THEN** the corresponding section explicitly says the figure is pending and does not fabricate an image or drawing

#### Scenario: Plan is rendered
- **WHEN** a construction plan is persisted
- **THEN** the Markdown structure and headings are deterministic regardless of provider prose or ordering

### Requirement: Cumulative construction-generation preview
The construction-plan service SHALL use the mainline cumulative-text callback contract, and the GUI SHALL display cumulative in-progress model output before replacing it with the final validated or fallback plan.

#### Scenario: Provider streams construction JSON
- **WHEN** output deltas arrive from Responses or compatible Chat streaming
- **THEN** the current-file preview receives cumulative text in arrival order and final artifacts are written only after validation

### Requirement: Self-contained migration and legacy retirement
The main project MUST own all migrated code, rules, tests, and packaged resources and MUST NOT import, read, configure, or fall back to the external `施工方案生成` directory. After focused verification passes, the project SHALL remove the replaced simplified method mapping, model-controlled complete-plan output, legacy Markdown renderer, and tests that permit model changes to evidence identities or deterministic methods.

#### Scenario: External migration source is unavailable
- **WHEN** the external source directory is renamed, moved, or deleted
- **THEN** construction generation, fallback, rendering, GUI workflow, and packaging continue using only main-project files

#### Scenario: Retired behavior is scanned
- **WHEN** migration verification completes
- **THEN** active code contains no external path reference, no alternate legacy construction renderer, and no contract that permits model-controlled release or evidence-set changes
