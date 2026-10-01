# Damage Report Generation

The report service consumes an inference `summary.json` and writes `report.json` and `report.md` beside it.

Configure an application credential separately from the Codex desktop login:

```powershell
$env:DAMAGE_REPORT_API_KEY = "your-application-key"
$env:DAMAGE_REPORT_BASE_URL = "https://api.openai.com/v1"
$env:DAMAGE_REPORT_MODEL = "gpt-5.6-sol"
```

For an OpenAI-compatible gateway, set `DAMAGE_REPORT_BASE_URL` to its API root. The SDK appends the Responses path. Do not place credentials in source files, `summary.json`, or report artifacts.

Generate a report from a completed inference directory:

```powershell
python scripts/generate_damage_report.py results/inference/example/summary.json
```

`report.json` is an auditable envelope containing the validated report, the exact structured evidence snapshot, and the report schema version. `report.md` is a human-readable rendering. Every generated report remains `pending_engineer_review` until a qualified reviewer updates it.
