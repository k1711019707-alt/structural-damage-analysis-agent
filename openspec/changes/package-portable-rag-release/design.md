## Context

The source application selects a project-local `knowledge_base/active_rag.json` before user data and validates the referenced SQLite and semantic artifacts at runtime. The current PyInstaller spec does not include that tree, hard-codes one Codex plugin script and one Node executable, and does not bundle the local sentence-transformer model needed to encode semantic queries. A GUI process may also be rebuilding production RAG while release work is prepared, so a build must never package live or half-written candidate state.

## Goals / Non-Goals

**Goals:**

- Produce a deterministic staging tree that contains only the active, healthy RAG runtime artifacts and a local embedding-model snapshot.
- Make FHL script and Node inputs explicit, validated build inputs with no developer-specific paths in source.
- Make the PyInstaller spec consume the immutable staging tree and include current RAG, knowledge-pipeline, assistant, and semantic-query dependencies.
- Verify bundled RAG health and offline semantic-query model loading from an extracted frozen candidate.
- Preserve a complete release manifest with relative paths and SHA-256 digests.

**Non-Goals:**

- Do not package credentials, user settings, inactive knowledge PDFs, historical RAG candidates, legacy databases, benchmarks, or training data.
- Do not rebuild, activate, roll back, or otherwise mutate production RAG as part of packaging.
- Do not change retrieval ranking, report/plan generation semantics, or the GUI knowledge-import workflow.
- Do not run a release build while the current background RAG rebuild is active.

## Decisions

1. **Stage immutable release resources before invoking PyInstaller.** A Python helper receives the project root, active-manifest path, local embedding-model source, FHL script, Node executable, and output staging directory. It validates all inputs, rejects a non-empty SQLite WAL, copies only required files, verifies source and destination digests, sanitizes machine-specific paths from the copied active manifest, and emits a staging manifest. PyInstaller consumes only this staging directory. Directly traversing the live knowledge root was rejected because it can mix versions during a rebuild.

2. **Treat the active manifest as the release selection boundary.** The staged knowledge tree contains `active_rag.json`, the selected SQLite database, semantic NPZ, semantic manifest, source documents listed by the active manifest, and available build/validation/activation evidence. Each active source document is copied only after its SHA-256 matches, is renamed only to avoid unsafe/colliding paths, and is referenced by a portable `source_files/...` path. It excludes `previous`, rollback paths, inactive source documents, converted/chunk intermediates, SQLite WAL/SHM files, and all non-selected candidate directories.

3. **Bundle the semantic encoder snapshot for offline query encoding.** The staging helper resolves the active semantic manifest's model name to an explicitly supplied local model directory or a local-only Hugging Face cache snapshot. It copies the resolved files under `embedding_models/<portable-name>`. Frozen semantic loading prefers this packaged directory and requests local-only model loading. Shipping only `semantic.npz` was rejected because it cannot embed new user queries.

4. **Make FHL build inputs explicit.** `build_portable.ps1` accepts `-FhlPluginScript`, `-NodeExe`, and `-SemanticModelPath`. Defaults may resolve from environment/PATH or a local-only model cache, but unresolved inputs fail with actionable messages. The spec reads only paths under the staging tree and contains no personal or installation-specific absolute path.

5. **Collect feature packages deliberately.** The spec keeps ordinary import analysis and additionally collects `runtime.assistant`, `knowledge_pipeline`, `sentence_transformers`, and their dynamically loaded Python/data dependencies. This is validated by importing and exercising the packaged paths, not by hidden-import strings alone.

6. **Keep bundled RAG read-only and layer writable user activation above it.** The frozen resource root supplies the initial active RAG only while no user active manifest exists. Frozen rebuilds write candidates and activation state below `%LOCALAPPDATA%`; a healthy user manifest then takes precedence. Rolling back/removing the user override reveals the bundled baseline again. Source mode retains its project-local production root. Packaging does not copy credentials or write into the extracted resource tree at runtime.

7. **Separate source tests from release proof.** Unit tests use temporary fixture databases, sidecars, manifests, model directories, and fake FHL/Node files. A formal release candidate is built only after the live background rebuild has completed; the extracted EXE must then report bundled RAG active/healthy and prove offline semantic query encoding.

8. **Build the source release from the same immutable resource stage.** The source archive copies a reviewed application-source allowlist plus the formal model, locked environment, staged production RAG, staged semantic encoder, and staged FHL/Node runtime. It excludes user API/settings files, caches, historical candidates, benchmarks, generated outputs, and developer-specific environment prefixes. Source runtime discovery uses the included local semantic model and Node executable when present.

## Risks / Trade-offs

- [Bundling the sentence-transformer model increases archive size] -> Include only the resolved snapshot required by the active semantic manifest and record every file in the release manifest.
- [A live RAG rebuild changes files during staging] -> Reject non-empty WAL state and compare source digests before and after each copy; never stage while the GUI rebuild is active.
- [Hugging Face cache is absent on another build machine] -> Require `-SemanticModelPath` or fail without network download.
- [FHL plugin or Node is absent] -> Require explicit local inputs and fail before PyInstaller; never silently ship a renderer that cannot launch.
- [Bundling active source documents increases size and carries distribution obligations] -> Include only documents bound to the final active manifest, verify their hashes, inventory them explicitly, and require the release owner to confirm distribution rights before promotion.
- [A frozen sync attempts to mutate `_internal`] -> Separate selected-manifest reads from writable activation targets and cover bundled-baseline/user-overlay precedence with tests.
- [PyInstaller misses dynamic ML modules] -> Add focused spec assertions, a diagnostic frozen build, and extracted-candidate runtime tests.

## Migration Plan

1. Add staging helper and temporary-fixture tests without touching production knowledge data.
2. Update semantic model resolution for frozen offline resources and add unit tests.
3. Update the build script and spec to consume staged resources and emit release metadata.
4. Run focused and full source tests plus strict OpenSpec validation.
5. After the background multi-document rebuild is confirmed complete, stage the newly active snapshot, build a versioned candidate, and validate the complete extracted onedir tree.
6. Build a separate versioned source candidate from the same reviewed stage and validate its inventory, startup diagnostics, active RAG, offline semantic model, formal model, and FHL runtime.
7. Keep the previous portable and source archives unchanged for rollback until both new candidates pass verification.

## Open Questions

- Distribution authorization for the active standards PDFs must be confirmed before candidate promotion; the packaging implementation preserves exact hashes but cannot establish legal permission.
- The final public product version and archive name remain part of the broader release-freeze decision; this change avoids overwriting existing releases.
