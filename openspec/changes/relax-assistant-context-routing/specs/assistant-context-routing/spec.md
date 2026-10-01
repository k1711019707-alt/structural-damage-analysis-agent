## ADDED Requirements

### Requirement: General knowledge questions SHALL NOT be blocked by detection-batch ambiguity

The assistant SHALL distinguish explicit detection-context questions from general engineering knowledge questions. A general knowledge question MUST continue to retrieval and generation even when multiple historical detection snapshots are available.

#### Scenario: General crack-classification question with multiple snapshots

- **WHEN** the user asks a question such as “裂缝损伤分为几个等级？是如何判定的” and several historical detection snapshots are registered
- **THEN** the assistant SHALL NOT return a batch-clarification answer and SHALL continue to knowledge-base retrieval and answer generation

#### Scenario: Explicit historical batch question

- **WHEN** the user explicitly provides a date, ordinal, path, image name, or asks for a detection/report/plan result
- **THEN** the assistant SHALL use all bounded matching detection context when it can be resolved, and an unresolved or ambiguous context SHALL neither block the answer nor produce a user-facing batch warning

### Requirement: Detection outputs SHALL be optional evidence context

The assistant SHALL treat structured detection results, reports, and construction plans as optional context equivalent to other supplied reference material. Their presence or absence SHALL NOT determine whether the model is allowed to answer.

#### Scenario: Detection context is available for a knowledge question

- **WHEN** a general question is asked while a current or historical detection result is available
- **THEN** the assistant MAY include the result, report, or plan as contextual evidence and SHALL still answer the knowledge question using the relevant evidence only

#### Scenario: Detection context is unavailable

- **WHEN** no unique detection run is resolved
- **THEN** the assistant SHALL continue with knowledge-base, web, and model-prior routing without returning a detection-context failure or ordinary batch warning

### Requirement: Knowledge-base-first answer routing

For every user question, the assistant SHALL attempt scoped active-v2 knowledge-base retrieval before selecting external fallback sources. A relevant knowledge-base result SHALL be supplied to the model together with instructions to combine it with the model's understanding and summarize the answer in the user's requested form.

#### Scenario: Relevant knowledge-base evidence exists

- **WHEN** active-v2 retrieval returns direct relevant anchors within the selected document scope
- **THEN** the assistant SHALL generate an answer using the retrieved evidence plus model reasoning, preserve verifiable knowledge-base citations, and SHALL NOT require a detection batch to be selected

#### Scenario: No relevant knowledge-base evidence exists

- **WHEN** active-v2 retrieval returns no direct relevant anchors or only a representative fallback
- **THEN** the assistant SHALL attempt configured web search when requested or useful and SHALL otherwise answer from model knowledge with an explicit freshness/evidence warning; the request SHALL NOT fail solely because the knowledge base missed

### Requirement: Evidence and safety boundaries SHALL remain visible internally

The assistant SHALL retain source manifests, retrieval warnings, web verification status, trace IDs, and engineer-review guidance, and SHALL continue to prohibit project-file mutation, command execution, credential disclosure, and fabricated citations.

#### Scenario: Model-prior fallback

- **WHEN** neither the knowledge base nor verified web search supplies sufficient evidence
- **THEN** the assistant SHALL label the answer as potentially based on general model knowledge, advise freshness review for time-sensitive claims, and retain the warning in the answer manifest
