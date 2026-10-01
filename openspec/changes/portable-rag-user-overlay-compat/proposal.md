## Why

The frozen portable application permits a writable per-user active RAG overlay. A user who previously ran an older build can therefore have a healthy but unrelated user `active_rag.json` containing only a small document set, while the GUI profile still carries the bundled release's active-v2 document IDs. Report generation then fails closed with `selected active-v2 document IDs are unavailable` even though every selected ID is present in the immutable bundled RAG.

## What Changes

- Revalidate a frozen user-overlay scope against the bundled active-RAG baseline when selected IDs are unavailable in the user overlay.
- Use the bundled baseline only when it is healthy and contains every explicitly selected document ID; preserve the user overlay for scopes it actually owns.
- Keep all bundled databases and manifests read-only and retain fail-closed behavior when neither source can satisfy the selected scope.
- Add regression coverage and rebuild a new versioned portable candidate without modifying the production RAG.

## Capabilities

### Modified Capabilities

- `portable-rag-release`: frozen retrieval tolerates stale/incompatible user overlays by selecting a compatible bundled baseline.

## Impact

- Affected runtime: `runtime/rag_production.py`, `runtime/knowledge_base.py`, and portable path tests.
- Affected release: a new versioned portable directory/archive; existing releases remain untouched.
- No production knowledge files are rebuilt, activated, deleted, or changed.
