## ADDED Requirements

### Requirement: Generation context SHALL support evidence-grounded damage summaries
The generation stage SHALL provide a dedicated damage-summary mode whose prompt requires separating observed evidence, possible explanations, required checks, recommended actions, limitations, and citations.

#### Scenario: Damage summary with evidence
- **WHEN** generation context is built with retrieved anchors or context groups and `generation_mode=damage-grounded-summary`
- **THEN** the generated prompt SHALL include the evidence context, source markers, and explicit instructions to avoid unsupported safety conclusions.

#### Scenario: Insufficient evidence
- **WHEN** no relevant chunks are provided or chunks are marked as fallback/unknown
- **THEN** the generation context SHALL state that evidence is insufficient and SHALL preserve `pending_engineer_review`.

### Requirement: Summary prompts SHALL preserve citation and review boundaries
The generation stage SHALL require citations for material claims and SHALL instruct the model to distinguish normative requirements from engineering suggestions.

#### Scenario: Citation requirement
- **WHEN** retrieved chunks contain source markers
- **THEN** the prompt SHALL require the model to cite the relevant `[KB:...]` marker for each material conclusion.

#### Scenario: Safety boundary
- **WHEN** the evidence concerns cracks, spalling, deformation, corrosion, or other structural damage
- **THEN** the prompt SHALL prohibit declaring a safety grade from appearance alone and SHALL require further inspection/review when evidence is incomplete.
