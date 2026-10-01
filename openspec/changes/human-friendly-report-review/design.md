## Context

`DamageReportReviewDialog` currently serializes the entire `DamageReport` to an editable `QPlainTextEdit` with wrapping disabled, then reparses that text during draft save or confirmation. The JSON includes system-owned schema, integrity, and provenance fields alongside normal review content. The persistence service already validates evidence correspondence, reviewer identity, determinate levels, and confirmation state, so the redesign must improve the editing surface without weakening those boundaries.

## Goals / Non-Goals

**Goals:**

- Make the default review workflow readable and editable for an engineering reviewer.
- Preserve canonical v3 values and every existing persistence/confirmation validation.
- Prevent the review UI from editing provenance, schema version, integrity, or finding identity.
- Keep full serialized data inspectable for diagnosis without making it an editing contract.
- Remain usable at the existing 1100x760 and 900x650 dialog sizes.

**Non-Goals:**

- Change `DamageReport`, report generation, persistence format, or construction-plan semantics.
- Add or remove findings, standards references, or evidence relationships in this dialog.
- Resolve knowledge references by guessing missing filenames, pages, or metadata.
- Replace the required human confirmation gate.

## Decisions

1. Use a `QTabWidget` with `总体信息`, `损伤明细`, `复核与局限`, and `原始数据` pages. Tabs keep the modal compact and make raw data explicitly secondary.
2. Use native line edits, wrapped plain-text editors, and combo boxes. Store each combo's canonical English level as item data while displaying Chinese labels.
3. Use a scroll area containing one group per finding. `image_name` and `finding_index` are read-only labels; editable controls cover only business content. Standards references are displayed as readable rows and preserved unchanged because the dialog is not a reference editor.
4. Rebuild an editable payload from a deep copy of `current_report`. Form values replace only the allowlisted subject, summary, level, finding-content, limitations, reviewer, and notes fields. Schema version, provenance, integrity, and finding identities therefore remain system-owned even if widget state is manipulated.
5. Synchronize a read-only JSON view from the reconstructed form when the advanced tab is selected and after draft persistence. Invalid intermediate form data leaves the last valid raw view visible and is reported when saving.
6. Render standard references from their structured `id`, `name`, and `role`. Raw `[KB:...]` tokens are removed from the visible role text only when a human-readable name or id is already present; no source or page is invented, and stored reference values remain unchanged.
7. Treat blank optional subject fields as `None`, blank required text as an empty string for schema validation, and limitations as non-empty trimmed lines.

## Risks / Trade-offs

- [Many findings can make the dialog tall] -> Put findings in a vertical scroll area and keep action buttons outside it.
- [Form and read-only JSON can drift] -> Generate advanced JSON from current controls on tab selection and after saves.
- [Future schema fields may not have controls] -> Start from a deep copy and replace only an explicit business-field allowlist, preserving unknown future system-owned values until dedicated controls are added.
- [Removing visible KB markers could hide useful detail] -> Always display available structured id/name and retain original reference objects unchanged in saved data.
- [Qt widgets expose mutable state to tests or extensions] -> Restore immutable model fields and identities from `current_report` during every reconstruction.

## Migration Plan

1. Add form reconstruction and structured tabs while retaining existing dialog entry points and button actions.
2. Replace JSON-editing tests with control-level behavior and immutability tests.
3. Run focused review/persistence tests and offscreen visual smoke at both supported sizes.
4. Roll back by reverting this change; persisted `damage-report.v3` documents remain compatible throughout.

## Open Questions

None.
