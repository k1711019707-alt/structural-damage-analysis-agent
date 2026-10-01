## Why

For multi-finding reports, the service correctly generates one bounded AI finding at a time, but the local merge step replaces all per-finding context with the generic text `视觉模型已完成 N 项损伤辅助判断，等待人工复核。` and the equally generic worst-level explanation. The review dialog therefore cannot summarize what this inspection found or why the overall auxiliary level was selected.

The same review also needs to verify that the repair-image prompt is actually applied to the provider request and that the real image-edit path preserves the original scene while changing only evidenced damage.

## What Changes

- Build a deterministic, evidence-grounded report summary from validated findings after chunked AI generation.
- Build a detailed overall-level reason that explains the worst-level rule, affected findings, and the non-safety-grade boundary.
- Keep single-finding provider-authored summaries compatible while making multi-finding merge output equally informative.
- Add prompt-contract coverage for FHL and SiliconFlow image-edit requests, including original-image anchoring, repair-method inclusion, no-new-object constraints, and provider-specific request routing.

## Capabilities

### Modified Capabilities

- `damage-report-review`: multi-finding reports must show an inspection summary and an auditable overall-level rationale.
- `repair-render-prompting`: enabled render requests must carry the configured prompt and reviewed repair method to the real image-edit provider.

## Impact

- `runtime/responses_damage_report.py`: deterministic summary/rationale assembly.
- `runtime/fhl_repair_renderer.py` and `runtime/siliconflow_repair_renderer.py`: prompt/request contract verification only where gaps are found.
- Focused report and renderer tests.
