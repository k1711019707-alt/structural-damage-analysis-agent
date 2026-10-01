# Tasks

## Planning and runtime

- [x] Create and strictly validate the OpenSpec proposal, requirements, design, and tasks.
- [x] Add ONNX Runtime provider detection and RapidOCR CUDA configuration.
- [x] Add GPU initialization/inference fallback to CPU with diagnostics.
- [x] Pass OCR backend status through the GUI import worker.
- [x] Switch the environment lock from CPU-only ONNX Runtime to `onnxruntime-gpu`.
- [x] Load pip CUDA 12 and cuDNN 9 DLLs in dependency order on Windows so mixed CUDA 11.8 PyTorch environments do not silently fall back to CPU.
- [x] Align PyTorch and torchvision with CUDA 12.8 so OCR, Docling, and semantic embedding can use GPU in the same process.
- [x] Reuse PyTorch's CUDA 12.8 runtime for ONNX Runtime and remove the duplicate NVIDIA CUDA 12.9 wheel stack.
- [x] Enable the existing visual blank-page review from GUI production synchronization and invalidate failed-page caches for review builds.

## Verification

- [x] Add provider-selection, fallback, injected-engine, and cancellation tests.
- [x] Verify the active environment provider list and OCR backend with a small scanned-page smoke test.
- [x] Run focused PDF/OCR, knowledge-base, GUI, and strict OpenSpec validation tests.
- [x] Verify the repaired runtime with all three real RapidOCR sessions and one real image inference on `CUDAExecutionProvider`.
- [x] Verify real CUDA inference for RapidOCR, BGE embedding, and the YOLO model after dependency alignment.
- [x] Rebuild and atomically activate the 20-document production RAG with healthy SQLite, FTS parity, semantic fingerprint, scoped retrieval, and reranking.
