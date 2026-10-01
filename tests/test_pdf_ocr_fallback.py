from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import ctypes
import os
import site

import numpy as np
import pytest


def test_cuda_dependency_preload_loads_pip_dlls_in_dependency_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import onnxruntime as ort
    import runtime.knowledge_base as knowledge_base

    names_by_package = {
        "cuda_runtime": ["cudart64_12.dll"],
        "nvjitlink": ["nvJitLink_120_0.dll"],
        "cuda_nvrtc": ["nvrtc-builtins64_129.dll", "nvrtc64_120_0.alt.dll", "nvrtc64_120_0.dll"],
        "cublas": ["cublas64_12.dll", "cublasLt64_12.dll"],
        "cufft": ["cufft64_11.dll", "cufftw64_11.dll"],
        "curand": ["curand64_10.dll"],
        "cudnn": [
            "cudnn64_9.dll",
            "cudnn_ops64_9.dll",
            "cudnn_adv64_9.dll",
            "cudnn_cnn64_9.dll",
            "cudnn_graph64_9.dll",
            "cudnn_heuristic64_9.dll",
            "cudnn_engines_runtime_compiled64_9.dll",
            "cudnn_engines_precompiled64_9.dll",
            "cudnn_engines_tensor_ir64_9.dll",
            "cudnn_ext64_9.dll",
        ],
    }
    nvidia_root = tmp_path / "nvidia"
    for package, names in names_by_package.items():
        bin_dir = nvidia_root / package / "bin"
        bin_dir.mkdir(parents=True)
        for name in names:
            (bin_dir / name).touch()

    events: list[str] = []
    monkeypatch.setattr(site, "getsitepackages", lambda: [str(tmp_path)])
    monkeypatch.setattr(os, "add_dll_directory", lambda path: events.append(f"dir:{Path(path).parent.name}") or object())
    monkeypatch.setattr(ctypes, "WinDLL", lambda path: events.append(Path(path).name) or object())
    monkeypatch.setattr(ort, "preload_dlls", lambda **_kwargs: events.append("ort.preload_dlls"))
    monkeypatch.setattr(knowledge_base, "_OCR_CUDA_DLL_HANDLES", [])
    monkeypatch.setattr(knowledge_base, "_OCR_CUDA_DLLS_PRELOADED", False)
    monkeypatch.setattr(knowledge_base, "_torch_cuda_dll_dir", lambda: None)

    knowledge_base.LocalOcrAdapter._preload_cuda_dependencies()

    loaded = [item for item in events if item.lower().endswith(".dll")]
    assert "nvrtc64_120_0.alt.dll" not in loaded
    assert loaded.index("cudart64_12.dll") < loaded.index("cublasLt64_12.dll")
    assert loaded.index("cublasLt64_12.dll") < loaded.index("cublas64_12.dll")
    assert loaded.index("cublas64_12.dll") < loaded.index("cufft64_11.dll")
    assert loaded.index("cufft64_11.dll") < loaded.index("curand64_10.dll")
    assert loaded.index("curand64_10.dll") < loaded.index("cudnn64_9.dll")
    assert events[-1] == "ort.preload_dlls"


def test_cuda_dependency_preload_prefers_locked_torch_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import onnxruntime as ort
    import runtime.knowledge_base as knowledge_base

    torch_lib = tmp_path / "torch" / "lib"
    torch_lib.mkdir(parents=True)
    for name in (
        "cudart64_12.dll",
        "nvJitLink_120_0.dll",
        "nvrtc-builtins64_128.dll",
        "nvrtc64_120_0.dll",
        "cublasLt64_12.dll",
        "cublas64_12.dll",
        "cufft64_11.dll",
        "curand64_10.dll",
        "cudnn64_9.dll",
        "cudnn_ops64_9.dll",
    ):
        (torch_lib / name).touch()
    events: list[str] = []
    preload_kwargs: list[dict[str, object]] = []
    monkeypatch.setattr(knowledge_base, "_torch_cuda_dll_dir", lambda: torch_lib)
    monkeypatch.setattr(os, "add_dll_directory", lambda path: events.append(f"dir:{path}") or object())
    monkeypatch.setattr(ctypes, "WinDLL", lambda path: events.append(Path(path).name) or object())
    monkeypatch.setattr(ort, "preload_dlls", lambda **kwargs: preload_kwargs.append(kwargs))
    monkeypatch.setattr(knowledge_base, "_OCR_CUDA_DLL_HANDLES", [])
    monkeypatch.setattr(knowledge_base, "_OCR_CUDA_DLLS_PRELOADED", False)

    knowledge_base.LocalOcrAdapter._preload_cuda_dependencies()

    loaded = [item for item in events if item.lower().endswith(".dll")]
    assert loaded.index("cudart64_12.dll") < loaded.index("cublasLt64_12.dll")
    assert loaded.index("cublas64_12.dll") < loaded.index("cufft64_11.dll")
    assert loaded.index("curand64_10.dll") < loaded.index("cudnn64_9.dll")
    assert preload_kwargs == [{"directory": str(torch_lib)}]


class FakeOcr:
    def __init__(self, texts: list[str] | None = None, error: Exception | None = None) -> None:
        self.texts = list(texts or [])
        self.error = error
        self.calls = 0

    def recognize(self, _image: object) -> str:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.texts.pop(0) if self.texts else ""


class FakeOrtSession:
    def __init__(self, provider: str) -> None:
        self.provider = provider

    def get_providers(self) -> list[str]:
        return [self.provider, "CPUExecutionProvider"]


class FakeRapidEngine:
    def __init__(self, provider: str, *, output: object | None = None, error: Exception | None = None) -> None:
        session = SimpleNamespace(session=FakeOrtSession(provider))
        self.text_det = SimpleNamespace(session=session)
        self.text_cls = SimpleNamespace(session=session)
        self.text_rec = SimpleNamespace(session=session)
        self.output = output if output is not None else {"texts": ["扫描识别文本"]}
        self.error = error
        self.calls = 0

    def __call__(self, _image: object) -> object:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.output


def test_local_ocr_adapter_selects_and_verifies_cuda_provider() -> None:
    from runtime.knowledge_base import LocalOcrAdapter

    params_seen: list[dict[str, object]] = []

    def factory(params: dict[str, object]) -> object:
        params_seen.append(dict(params))
        return FakeRapidEngine("CUDAExecutionProvider")

    adapter = LocalOcrAdapter(
        prefer_gpu=True,
        device_id=0,
        engine_factory=factory,
        available_providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
    )

    assert adapter.recognize(object()) == "扫描识别文本"
    assert params_seen == [{
        "EngineConfig.onnxruntime.use_cuda": True,
        "EngineConfig.onnxruntime.cuda_ep_cfg.device_id": 0,
    }]
    assert adapter.runtime_info.actual_provider == "CUDAExecutionProvider"
    assert "OCR GPU" in adapter.backend_label


def test_prepare_runtime_preloads_cuda_before_ocr_initialization(monkeypatch: pytest.MonkeyPatch) -> None:
    from runtime.knowledge_base import LocalOcrAdapter

    calls: list[str] = []
    adapter = LocalOcrAdapter(
        prefer_gpu=True,
        engine_factory=lambda _params: FakeRapidEngine("CUDAExecutionProvider"),
        available_providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
    )
    monkeypatch.setattr(adapter, "_preload_cuda_dependencies", lambda: calls.append("preloaded"))

    adapter.prepare_runtime()

    assert calls == ["preloaded"]
    assert adapter._engine is None


def test_local_ocr_adapter_uses_cpu_when_cuda_provider_is_unavailable() -> None:
    from runtime.knowledge_base import LocalOcrAdapter

    params_seen: list[dict[str, object]] = []

    def factory(params: dict[str, object]) -> object:
        params_seen.append(dict(params))
        return FakeRapidEngine("CPUExecutionProvider")

    adapter = LocalOcrAdapter(
        prefer_gpu=True,
        engine_factory=factory,
        available_providers=["CPUExecutionProvider"],
    )

    assert adapter.recognize(object()) == "扫描识别文本"
    assert params_seen == [{"EngineConfig.onnxruntime.use_cuda": False}]
    assert adapter.runtime_info.actual_provider == "CPUExecutionProvider"
    assert "已自动回退" in adapter.backend_label


def test_local_ocr_adapter_falls_back_when_cuda_session_is_not_active() -> None:
    from runtime.knowledge_base import LocalOcrAdapter

    params_seen: list[dict[str, object]] = []

    def factory(params: dict[str, object]) -> object:
        params_seen.append(dict(params))
        return FakeRapidEngine("CPUExecutionProvider")

    adapter = LocalOcrAdapter(
        prefer_gpu=True,
        engine_factory=factory,
        available_providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
    )

    assert adapter.recognize(object()) == "扫描识别文本"
    assert [item["EngineConfig.onnxruntime.use_cuda"] for item in params_seen] == [True, False]
    assert "未实际启用 CUDA" in adapter.runtime_info.fallback_reason
    assert adapter.runtime_info.actual_provider == "CPUExecutionProvider"


def test_local_ocr_adapter_retries_once_on_cpu_after_gpu_inference_failure() -> None:
    from runtime.knowledge_base import LocalOcrAdapter

    gpu = FakeRapidEngine("CUDAExecutionProvider", error=RuntimeError("CUDA execution failed"))
    cpu = FakeRapidEngine("CPUExecutionProvider", output={"texts": ["CPU 回退成功"]})
    engines = iter([gpu, cpu])

    adapter = LocalOcrAdapter(
        prefer_gpu=True,
        engine_factory=lambda _params: next(engines),
        available_providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
    )

    assert adapter.recognize(object()) == "CPU 回退成功"
    assert gpu.calls == 1
    assert cpu.calls == 1
    assert adapter.runtime_info.actual_provider == "CPUExecutionProvider"
    assert "GPU 推理失败" in adapter.runtime_info.fallback_reason


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ({"txts": np.array(["第一行", "第二行"])}, "第一行 第二行"),
        ({"texts": np.array(["第三行", "第四行"])}, "第三行 第四行"),
        ({"txts": np.array([], dtype=object)}, ""),
        ({"texts": np.array([], dtype=object)}, ""),
        ({"txts": np.array([], dtype=object), "texts": np.array(["备用字段文本"])}, "备用字段文本"),
        (SimpleNamespace(txts=np.array(["对象第一行", "对象第二行"])), "对象第一行 对象第二行"),
        ([[None, "列表第一行", 0.9], [None, "列表第二行", 0.8]], "列表第一行 列表第二行"),
    ],
)
def test_local_ocr_adapter_normalizes_array_dict_object_and_list_results(output: object, expected: str) -> None:
    from runtime.knowledge_base import LocalOcrAdapter

    adapter = LocalOcrAdapter(engine=lambda _image: output)

    assert adapter.recognize(object()) == expected


def test_convert_pdf_falls_back_to_recognize_after_empty_structured_array(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import pymupdf

    from knowledge_pipeline import pdf_convert
    from runtime.knowledge_base import LocalOcrAdapter

    path = tmp_path / "structured-empty.pdf"
    document = pymupdf.open()
    document.new_page()
    document.save(str(path))
    document.close()
    outputs = iter(
        [
            SimpleNamespace(boxes=np.empty((0, 4, 2)), txts=np.array([], dtype=object), scores=np.array([], dtype=float)),
            {"texts": np.array(["回退识别得到有效文本内容"]), "scores": np.array([0.95])},
        ]
    )
    adapter = LocalOcrAdapter(engine=lambda _image: next(outputs))
    monkeypatch.setattr(pdf_convert, "_render_page", lambda *_args: object())
    options = pdf_convert.PdfConversionOptions(use_docling=False, use_camelot=False, use_pdfplumber=False)

    result = pdf_convert.convert_pdf(path, ocr=adapter, options=options)

    assert result.status.status == "ready"
    assert result.quality_report.low_quality_pages == []
    assert result.quality_report.successfully_recovered_ocr_pages == [1]
    assert result.pages[0].extraction_method == "ocr:rapidocr"
    assert result.pages[0].ocr_text_chars > 0
    assert result.quality_report.failed_pages == []


def test_text_pdf_does_not_invoke_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    import runtime.knowledge_base as knowledge_base

    ocr = FakeOcr(error=AssertionError("OCR must not run"))
    native_text = "混凝土结构检测技术标准原生文本内容"
    monkeypatch.setattr(knowledge_base, "_native_pdf_pages", lambda _path: [(1, native_text)])

    assert knowledge_base._extract_pdf(Path("text.pdf"), ocr=ocr) == [("page:1", native_text)]
    assert ocr.calls == 0


def test_scanned_pdf_uses_ocr_with_page_location(monkeypatch: pytest.MonkeyPatch) -> None:
    import runtime.knowledge_base as knowledge_base

    ocr = FakeOcr(["扫描页混凝土裂缝检测"])
    monkeypatch.setattr(knowledge_base, "_native_pdf_pages", lambda _path: [(1, "")])
    monkeypatch.setattr(knowledge_base, "_render_pdf_page", lambda _path, page_number: f"image-{page_number}")

    assert knowledge_base._extract_pdf(Path("scan.pdf"), ocr=ocr) == [("page:1", "扫描页混凝土裂缝检测")]
    assert ocr.calls == 1


def test_pymupdf_native_text_is_used_when_pypdf_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    import runtime.knowledge_base as knowledge_base

    native_text = "PyMuPDF 提取到的混凝土现场检测标准文本"
    ocr = FakeOcr(error=AssertionError("OCR must not run"))

    def fail_pypdf(_path: Path) -> list[tuple[int, str]]:
        raise RuntimeError("AES dependency unavailable")

    monkeypatch.setattr(knowledge_base, "_native_pdf_pages", fail_pypdf)
    monkeypatch.setattr(knowledge_base, "_pymupdf_native_pages", lambda _path: [(1, native_text)])

    assert knowledge_base._extract_pdf(Path("encrypted.pdf"), ocr=ocr) == [("page:1", native_text)]
    assert ocr.calls == 0


def test_mixed_pdf_only_ocr_scanned_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    import runtime.knowledge_base as knowledge_base

    ocr = FakeOcr(["第二页 OCR 文本"])
    monkeypatch.setattr(
        knowledge_base,
        "_native_pdf_pages",
        lambda _path: [
            (1, "第一页具有足够长度的原生结构检测文本"),
            (2, ""),
            (3, "第三页具有足够长度的原生施工验收文本"),
        ],
    )
    monkeypatch.setattr(knowledge_base, "_render_pdf_page", lambda _path, page_number: f"image-{page_number}")

    assert knowledge_base._extract_pdf(Path("mixed.pdf"), ocr=ocr) == [
        ("page:1", "第一页具有足够长度的原生结构检测文本"),
        ("page:2", "第二页 OCR 文本"),
        ("page:3", "第三页具有足够长度的原生施工验收文本"),
    ]
    assert ocr.calls == 1


def test_pdf_extraction_reports_page_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    import runtime.knowledge_base as knowledge_base

    monkeypatch.setattr(
        knowledge_base,
        "_native_pdf_pages",
        lambda _path: [(1, "第一页具有足够长度的原生结构检测文本"), (2, "第二页具有足够长度的原生施工验收文本")],
    )
    progress: list[tuple[int, int]] = []
    knowledge_base._extract_pdf(Path("progress.pdf"), progress=lambda page, total: progress.append((page, total)))
    assert progress == [(1, 2), (2, 2)]


def test_pdf_extraction_can_be_cancelled_between_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    import runtime.knowledge_base as knowledge_base

    monkeypatch.setattr(
        knowledge_base,
        "_native_pdf_pages",
        lambda _path: [(1, "第一页具有足够长度的原生结构检测文本"), (2, "第二页具有足够长度的原生施工验收文本")],
    )
    with pytest.raises(knowledge_base.DocumentExtractionCancelled, match="已取消"):
        knowledge_base._extract_pdf(Path("cancel.pdf"), should_stop=lambda: True)


def test_blank_scanned_pdf_remains_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    import runtime.knowledge_base as knowledge_base

    monkeypatch.setattr(knowledge_base, "_native_pdf_pages", lambda _path: [(1, "")])
    monkeypatch.setattr(knowledge_base, "_render_pdf_page", lambda _path, _page_number: object())

    with pytest.raises(knowledge_base.DocumentExtractionError, match="OCR 无结果"):
        knowledge_base._extract_pdf(Path("blank.pdf"), ocr=FakeOcr([""]))


def test_ocr_dependency_failure_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    import runtime.knowledge_base as knowledge_base

    monkeypatch.setattr(knowledge_base, "_native_pdf_pages", lambda _path: [(1, "")])
    monkeypatch.setattr(knowledge_base, "_render_pdf_page", lambda _path, _page_number: object())
    error = knowledge_base.DocumentExtractionError("扫描 PDF 需要本地 OCR 依赖 rapidocr")

    with pytest.raises(knowledge_base.DocumentExtractionError, match="rapidocr"):
        knowledge_base._extract_pdf(Path("missing-ocr.pdf"), ocr=FakeOcr(error=error))
