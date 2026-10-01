## 1. OpenSpec and implementation

- [x] 1.1 Validate the change artifacts with strict OpenSpec validation.
- [x] 1.2 Inspect the current conversion contracts and add local header-review options/helpers.
- [x] 1.3 Integrate page-level header OCR matching, conservative replacement, audit metadata, and quality counters.

## 2. Verification

- [x] 2.1 Run compile/static checks with the project interpreter.
- [x] 2.2 Run the hard-coded PDF conversion test and verify all three page headers read `建材发展导向`.
- [x] 2.3 Verify `original_text`, OCR text, confidence, status, and quality counters in `conversion.json`.
- [x] 2.4 Run chunk, index, and retrieve tests against the corrected conversion output.
