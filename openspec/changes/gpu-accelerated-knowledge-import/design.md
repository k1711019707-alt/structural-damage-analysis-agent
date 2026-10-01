# Design: GPU 加速知识库导入

## Runtime flow

```text
KnowledgeBaseImportWorker
  -> LocalOcrAdapter(prefer_gpu=True)
     -> onnxruntime.get_available_providers()
     -> preload pip CUDA/cuDNN DLLs, including split cuDNN libraries
     -> RapidOCR(EngineConfig.onnxruntime.use_cuda=True/False)
  -> scanned-page OCR
  -> GPU failure: recreate CPU adapter and retry page once
  -> progress/backend diagnostics
```

`LocalOcrAdapter` remains lazy so text-only PDFs do not import or initialize OCR models. The adapter determines GPU availability from ONNX Runtime, not PyTorch alone: a CUDA-enabled PyTorch build cannot prove that RapidOCR has a CUDA execution provider. Its diagnostics expose `requested_backend`, `actual_provider`, `device_id`, `fallback_reason`, and `ocr_used`.

RapidOCR receives `EngineConfig.onnxruntime.use_cuda=true` and device ID 0 only when `CUDAExecutionProvider` is reported. The provider list still includes CPU for unsupported operators. If initialization or page inference fails, a new CPU adapter is created and only the failed page is retried once; the original source text and page provenance remain unchanged.

On Windows, the adapter reuses the CUDA 12.8/cuDNN 9 DLLs bundled in the locked PyTorch wheel and explicitly loads CUDA Runtime, NVJitLink/NVRTC, cuBLAS, cuFFT, cuRAND, and cuDNN in dependency order before creating GPU sessions. A legacy `nvidia/*/bin` resolver remains only as a compatibility fallback when the locked PyTorch runtime is unavailable. PDF conversion invokes this preparation before Docling because Docling can create its own RapidOCR sessions before the page-level adapter is first used. The adapter verifies all three project RapidOCR session providers both after initialization and after inference. This catches missing transitive DLLs such as `cublasLt64_12.dll` or `cufft64_11.dll` and ONNX Runtime's internal CUDA-to-CPU fallback instead of reporting a session that only appeared to be GPU-backed.

The GUI worker creates one adapter per imported batch and includes a compact backend label in progress messages. No GPU claim is made for native PDF extraction, chunking, SQLite writes, or a CPU-only provider list.

The local near-blank review also runs for pages preflighted as `blank_or_unreadable`, including true vector-PDF blank pages that contain no raster image. It still requires negligible rendered foreground, no significant connected component, no structured table, and at most page-relative micro text before excluding the page from the content denominator. This prevents a genuine blank separator page from becoming a failed OCR page without weakening the failed-page gate for meaningful sparse content.

GUI production synchronization enables the existing `gpt-5.5` blank-page review for candidates that remain `blank_or_unreadable` after local review. A remote-review build does not reuse a conversion cache that still contains failed pages, so an earlier failure cannot bypass the newly enabled review; clean Docling conversions remain reusable.

## Compatibility

- `onnxruntime-gpu==1.23.2` replaces CPU-only `onnxruntime` and reuses PyTorch's CUDA 12.8/cuDNN 9 runtime. PyTorch and torchvision remain on the existing 2.7.1/0.22.1 release pair but use their CUDA 12.8 builds, preventing same-process cuDNN DLL conflicts between OCR, Docling, BGE embedding, and YOLO while avoiding a second 2.4 GiB NVIDIA wheel stack.
- Existing injected fake OCR engines continue to work and are treated as externally managed backend `custom`.
- CPU fallback is deterministic and preserves current exceptions/cancellation contracts.
