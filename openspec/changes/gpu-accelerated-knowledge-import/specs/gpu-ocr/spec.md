# GPU 加速知识库导入

## ADDED Requirements

### Requirement: Automatic OCR backend selection
The knowledge-base importer SHALL use RapidOCR with `CUDAExecutionProvider` when the active ONNX Runtime exposes that provider, and SHALL use CPUExecutionProvider otherwise.

#### Scenario: CUDA provider is available
- **WHEN** a scanned PDF page requires OCR and ONNX Runtime reports `CUDAExecutionProvider`
- **THEN** the importer SHALL initialize RapidOCR with CUDA enabled, keep CPU as a fallback provider, and record the actual provider/device in diagnostics.

#### Scenario: CUDA provider is unavailable
- **WHEN** a scanned PDF page requires OCR but the runtime does not expose `CUDAExecutionProvider`
- **THEN** the importer SHALL use CPU OCR, complete normally, and report that GPU OCR is unavailable without failing the import.

### Requirement: Safe runtime fallback
GPU initialization or inference failure SHALL fall back to a CPU RapidOCR instance for the current document, preserving page progress, extracted text, cancellation, and completed records.

#### Scenario: GPU initialization fails
- **WHEN** RapidOCR cannot initialize a CUDA-backed session
- **THEN** the importer SHALL recreate the OCR adapter in CPU mode and continue with an explicit fallback warning.

#### Scenario: GPU inference fails
- **WHEN** a CUDA OCR call raises a provider/runtime error
- **THEN** the importer SHALL retry that page with CPU OCR at most once and SHALL retain a diagnostic reason.

### Requirement: Backend observability
The GUI SHALL display whether the current import is using GPU OCR, CPU OCR, or CPU fallback, without claiming GPU acceleration when the active provider is CPU.

#### Scenario: Import status
- **WHEN** an import worker starts or reports page progress
- **THEN** its status text SHALL include the detected OCR backend and provider availability; text-only documents MAY report that OCR was not needed.

### Requirement: Dependency compatibility
The project environment SHALL prefer a CUDA-capable ONNX Runtime package while retaining deterministic CPU fallback for machines without CUDA or with incompatible driver/provider libraries.

#### Scenario: CPU-only installation
- **WHEN** only CPUExecutionProvider is installed
- **THEN** the importer SHALL remain usable and shall not attempt to fabricate GPU status.
