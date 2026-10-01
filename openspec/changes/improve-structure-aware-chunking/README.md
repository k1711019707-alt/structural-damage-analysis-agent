# improve-structure-aware-chunking

Upgrade the knowledge-pipeline chunk stage to consume the latest structured
`DocumentConversion` output, preserve provenance, and emit structure-aware,
token-aware parent/child chunks for higher-recall retrieval. Keep PDF
conversion behavior stable except for duplicate projection/warning fixes and
metadata needed by the chunk stage.

Upgrade knowledge_pipeline chunking to structure-aware, token-aware parent-child chunks aligned with pdf_convert output; fix minimal conversion provenance issues.
