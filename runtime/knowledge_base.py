"""Offline document extraction, chunking, indexing, and scoped retrieval."""
from __future__ import annotations

import hashlib
import logging
import re
import shutil
import sqlite3
import threading
import zipfile
from contextlib import closing
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable
from xml.etree import ElementTree


SUPPORTED_EXTENSIONS = {".pdf", ".doc", ".docx"}
MAX_SEARCH_TOKENS = 24
OCR_MIN_TEXT_CHARS = 12
OCR_RENDER_SCALE = 2.0
IGNORED_QUERY_TOKENS = {
    "results", "damage_findings", "class_name", "class_id", "score",
    "status", "success", "image_name", "image_path", "area", "box",
    "screening_severity", "detection_confidence", "index", "true", "false",
}
_SOURCE_SUFFIX_RE = re.compile(r"\.(?:pdf|docx?|txt|md)$", re.I)


LOGGER = logging.getLogger(__name__)
_RAG_RUNTIME_CACHE_LOCK = threading.RLock()
_RAG_RUNTIME_CACHE: dict[str, Any] = {}
_OCR_CUDA_DLL_LOCK = threading.RLock()
_OCR_CUDA_DLL_HANDLES: list[Any] = []
_OCR_CUDA_DLLS_PRELOADED = False
_OCR_NVIDIA_DLL_LOAD_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("cuda_runtime", ("cudart64_*.dll",)),
    ("nvjitlink", ("nvJitLink_*.dll",)),
    ("cuda_nvrtc", ("nvrtc-builtins64_*.dll", "nvrtc64_*.dll")),
    ("cublas", ("cublasLt64_*.dll", "cublas64_*.dll")),
    ("cufft", ("cufft64_*.dll", "cufftw64_*.dll")),
    ("curand", ("curand64_*.dll",)),
    (
        "cudnn",
        (
            "cudnn64_*.dll",
            "cudnn_ops64_*.dll",
            "cudnn_adv64_*.dll",
            "cudnn_cnn64_*.dll",
            "cudnn_graph64_*.dll",
            "cudnn_heuristic64_*.dll",
            "cudnn_engines_runtime_compiled64_*.dll",
            "cudnn_engines_precompiled64_*.dll",
            "cudnn_engines_tensor_ir64_*.dll",
            "cudnn_ext64_*.dll",
        ),
    ),
)


def _nvidia_cuda_dlls_in_load_order(nvidia_roots: Iterable[Path]) -> list[Path]:
    """Resolve pip CUDA/cuDNN DLLs in dependency order for Windows loading."""
    ordered: list[Path] = []
    seen_names: set[str] = set()
    for package, patterns in _OCR_NVIDIA_DLL_LOAD_GROUPS:
        for root in nvidia_roots:
            bin_dir = root / package / "bin"
            if not bin_dir.is_dir():
                continue
            for pattern in patterns:
                for library in sorted(bin_dir.glob(pattern), key=lambda item: item.name.lower()):
                    # NVIDIA ships an alternate NVRTC binary beside the primary DLL.
                    # ONNX Runtime expects the primary filename and does not require both.
                    if ".alt." in library.name.lower():
                        continue
                    key = library.name.lower()
                    if key in seen_names:
                        continue
                    seen_names.add(key)
                    ordered.append(library)
    return ordered


def _flat_cuda_dlls_in_load_order(dll_dir: Path) -> list[Path]:
    """Resolve a self-contained CUDA DLL directory in dependency order."""
    ordered: list[Path] = []
    seen_names: set[str] = set()
    if not dll_dir.is_dir():
        return ordered
    for _group, patterns in _OCR_NVIDIA_DLL_LOAD_GROUPS:
        for pattern in patterns:
            for library in sorted(dll_dir.glob(pattern), key=lambda item: item.name.lower()):
                if ".alt." in library.name.lower():
                    continue
                key = library.name.lower()
                if key in seen_names:
                    continue
                seen_names.add(key)
                ordered.append(library)
    return ordered


def _torch_cuda_dll_dir() -> Path | None:
    """Return the bundled CUDA 12 runtime from the locked PyTorch build."""
    try:
        import torch

        cuda_version = str(getattr(torch.version, "cuda", "") or "")
        candidate = Path(torch.__file__).resolve().parent / "lib"
        if cuda_version.startswith("12.") and candidate.is_dir():
            return candidate
    except (ImportError, OSError, AttributeError):
        pass
    return None


def _runtime_file_identity(path: Path, stat: Any) -> tuple[int, int, int]:
    identity = (int(getattr(stat, "st_dev", 0)), int(getattr(stat, "st_ino", 0)), int(getattr(stat, "st_ctime_ns", 0)))
    if not any(identity) and path.is_file():
        try:
            import ctypes
            import msvcrt

            class _FileInfo(ctypes.Structure):
                _fields_ = [
                    ("dwFileAttributes", ctypes.c_uint32),
                    ("ftCreationTimeLow", ctypes.c_uint32),
                    ("ftCreationTimeHigh", ctypes.c_uint32),
                    ("dwVolumeSerialNumber", ctypes.c_uint32),
                    ("nFileSizeHigh", ctypes.c_uint32),
                    ("nFileSizeLow", ctypes.c_uint32),
                    ("nNumberOfLinks", ctypes.c_uint32),
                    ("nFileIndexHigh", ctypes.c_uint32),
                    ("nFileIndexLow", ctypes.c_uint32),
                ]

            with path.open("rb") as stream:
                info = _FileInfo()
                if ctypes.windll.kernel32.GetFileInformationByHandle(  # type: ignore[attr-defined]
                    msvcrt.get_osfhandle(stream.fileno()), ctypes.byref(info)
                ):
                    return (
                        int(info.dwVolumeSerialNumber),
                        int(info.nFileIndexHigh),
                        int(info.nFileIndexLow),
                    )
        except (ImportError, OSError, AttributeError, ValueError):
            pass
    return identity


def _runtime_file_fingerprint(path: str | Path | None, declared_sha256: str = "") -> tuple[Any, ...]:
    if path is None or not str(path).strip():
        return "", False, 0, 0, (0, 0, 0), str(declared_sha256 or "")
    target = Path(path).resolve()
    try:
        stat = target.stat()
        return (
            str(target), target.is_file(), int(stat.st_size), int(stat.st_mtime_ns),
            _runtime_file_identity(target, stat), str(declared_sha256 or ""),
        )
    except OSError:
        return str(target), False, 0, 0, (0, 0, 0), str(declared_sha256 or "")


def _readonly_sqlite(path: str | Path) -> sqlite3.Connection:
    target = Path(path).resolve()
    connection = sqlite3.connect(target.as_uri() + "?mode=ro", uri=True, timeout=5.0)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def _active_runtime_state(manifest_path: str | Path | None = None) -> tuple[dict[str, Any], Any | None, tuple[str, ...]]:
    """Revalidate live activation bytes and cache only digest-bound semantic arrays."""
    from runtime import rag_production
    from runtime.app_paths import active_rag_manifest_path

    status_function = rag_production.active_rag_status
    selected_manifest_path = Path(manifest_path).resolve() if manifest_path is not None else active_rag_manifest_path()
    manifest_fingerprint = _runtime_file_fingerprint(selected_manifest_path)
    warnings: list[str] = []
    try:
        status = (
            status_function(manifest_path=selected_manifest_path)
            if manifest_path is not None
            else status_function()
        )
        if not isinstance(status, dict):
            raise TypeError("active_rag_status returned a non-mapping value")
    except Exception as exc:
        LOGGER.exception("Active RAG status inspection failed")
        status = {"active": False, "reason": "activation_check_failed"}
        warnings.append(f"active_status_unavailable:{type(exc).__name__}")

    database_path = (status.get("database") or {}).get("path")
    active_manifest = status.get("manifest") if isinstance(status.get("manifest"), dict) else {}
    semantic_path: Path | None = None
    semantic_manifest_path: Path | None = None
    semantic = None
    semantic_health = status.get("semantic")
    if not isinstance(semantic_health, dict):
        semantic_health = (
            (status.get("database") or {}).get("semantic")
            if isinstance(status.get("database"), dict)
            else {}
        )
    if not isinstance(semantic_health, dict):
        semantic_health = {}
    if status.get("active") and bool(active_manifest.get("semantic_index_path")):
        try:
            from knowledge_pipeline.semantic_retrieve import SemanticRetriever

            semantic_path_value = semantic_health.get("path")
            if semantic_path_value:
                semantic_path = Path(str(semantic_path_value)).resolve()
            else:
                semantic_path = rag_production._resolve_portable_path(
                    active_manifest.get("semantic_index_path"), manifest_path=selected_manifest_path
                )
            if semantic_path is None:
                raise FileNotFoundError("configured semantic sidecar path could not be resolved")
            semantic_manifest_path = (
                Path(str(semantic_health["manifest_path"])).resolve()
                if semantic_health.get("manifest_path")
                else Path(str(semantic_path) + ".manifest.json")
            )
            semantic_index_sha256 = str(
                active_manifest.get("semantic_index_sha256")
                or semantic_health.get("index_sha256")
                or semantic_health.get("sha256")
                or ""
            )
            semantic_manifest_sha256 = str(
                active_manifest.get("semantic_manifest_sha256")
                or semantic_health.get("manifest_sha256")
                or ""
            )
            semantic_key = (
                str(semantic_path),
                semantic_index_sha256,
                str(semantic_manifest_path),
                semantic_manifest_sha256,
                _runtime_file_fingerprint(semantic_path),
                _runtime_file_fingerprint(semantic_manifest_path),
            )
            with _RAG_RUNTIME_CACHE_LOCK:
                cached = dict(_RAG_RUNTIME_CACHE)
            if (
                cached.get("status_function_id") == id(status_function)
                and cached.get("manifest_path") == str(selected_manifest_path)
                and cached.get("semantic_key") == semantic_key
            ):
                semantic = cached.get("semantic")
            else:
                semantic_kwargs: dict[str, str] = {}
                if semantic_index_sha256:
                    semantic_kwargs["expected_index_sha256"] = semantic_index_sha256
                if semantic_manifest_sha256:
                    semantic_kwargs["expected_manifest_sha256"] = semantic_manifest_sha256
                semantic = SemanticRetriever(semantic_path, **semantic_kwargs)
        except Exception as exc:
            LOGGER.exception("Semantic retriever initialization failed")
            semantic = None
            warnings.append(f"semantic_unavailable:{type(exc).__name__}")

    semantic_key = (
        str(semantic_path or ""),
        str(
            active_manifest.get("semantic_index_sha256")
            or semantic_health.get("index_sha256")
            or semantic_health.get("sha256")
            or ""
        ),
        str(semantic_manifest_path or ""),
        str(active_manifest.get("semantic_manifest_sha256") or semantic_health.get("manifest_sha256") or ""),
        _runtime_file_fingerprint(semantic_path),
        _runtime_file_fingerprint(semantic_manifest_path),
    )

    fingerprint = (
        manifest_fingerprint,
        _runtime_file_fingerprint(database_path, str(active_manifest.get("database_sha256") or "")),
        _runtime_file_fingerprint(
            semantic_path,
            str(active_manifest.get("semantic_index_sha256") or active_manifest.get("semantic_sha256") or ""),
        ),
        _runtime_file_fingerprint(semantic_manifest_path),
    )
    with _RAG_RUNTIME_CACHE_LOCK:
        _RAG_RUNTIME_CACHE.clear()
        _RAG_RUNTIME_CACHE.update({
            "status_function_id": id(status_function),
            "manifest_path": str(selected_manifest_path),
            "status": status,
            "semantic": semantic,
            "semantic_path": semantic_path,
            "semantic_manifest_path": semantic_manifest_path,
            "semantic_key": semantic_key,
            "warnings": tuple(warnings),
            "fingerprint": fingerprint,
        })
    return status, semantic, tuple(warnings)


def _reset_rag_runtime_cache_for_tests() -> None:
    with _RAG_RUNTIME_CACHE_LOCK:
        _RAG_RUNTIME_CACHE.clear()


def _search_tokens(query: str) -> list[str]:
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", " ", str(query)).strip()
    tokens: list[str] = []
    seen: set[str] = set()
    for raw in normalized.split():
        token = raw.strip().casefold()
        if token in seen or token in IGNORED_QUERY_TOKENS:
            continue
        if token.isdigit() or len(token) < 2:
            continue
        seen.add(token)
        tokens.append(token)
        if len(tokens) >= MAX_SEARCH_TOKENS:
            break
    return tokens


def _normalize_source_title(value: str) -> str:
    title = _SOURCE_SUFFIX_RE.sub("", str(value or "")).casefold()
    title = re.sub(r"(?:\(\d+\)|（\d+）)$", "", title).strip()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", title)


@dataclass
class DocumentRecord:
    document_id: str
    path: str
    name: str
    extension: str
    size_bytes: int
    modified_ns: int
    sha256: str
    status: str
    folder_id: str = ""
    error: str = ""


@dataclass
class KnowledgeBaseFolder:
    folder_id: str
    parent_id: str
    name: str
    relative_path: str


@dataclass
class KnowledgeBaseChunk:
    chunk_id: str
    document_id: str
    location: str
    text: str
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    source_marker_value: str = ""

    @property
    def source_marker(self) -> str:
        return self.source_marker_value or f"[KB:{self.document_id}:{self.location}]"


@dataclass(frozen=True)
class KnowledgeBaseSearchResult:
    """Retrieval result with explicit provenance for scoped fallback."""

    chunks: tuple[KnowledgeBaseChunk, ...]
    scope_available: bool
    used_scoped_fallback: bool = False
    scope_document_ids: tuple[str, ...] = ()
    anchors: tuple[dict[str, Any], ...] = ()
    context_groups: tuple[dict[str, Any], ...] = ()
    retrieval_mode: str = "lexical"
    warnings: tuple[str, ...] = ()
    route_diagnostics: dict[str, Any] = field(default_factory=dict)
    reranker: str = ""
    rerank_status: str = "not_run"
    rerank_candidate_count: int = 0
    rerank_selected_count: int = 0


class ActiveRagScopeAdapter:
    """Non-persistent scope adapter used after the legacy catalog cutover.

    GUI profiles carry explicit v2 document IDs.  Folder IDs are retained for
    backward-compatible settings display, but folder-only retrieval is rejected
    instead of consulting or recreating the legacy SQLite catalog.
    """

    db_path = Path("")

    @staticmethod
    def _resolve_scope_document_ids(
        *, document_ids: Iterable[str] | None = None, folder_ids: Iterable[str] | None = None
    ) -> list[str]:
        requested = sorted({str(item) for item in (document_ids or []) if str(item)})
        if requested:
            return requested
        return []


class DocumentExtractionError(RuntimeError):
    pass


class DocumentExtractionCancelled(DocumentExtractionError):
    pass


@dataclass
class OcrRuntimeInfo:
    """Observable OCR execution state for import diagnostics."""

    requested_backend: str
    actual_provider: str = "not_initialized"
    device_id: int | None = None
    fallback_reason: str = ""
    ocr_used: bool = False


class LocalOcrAdapter:
    """Lazy local OCR adapter backed by RapidOCR and ONNX Runtime."""

    def __init__(
        self,
        engine: object | None = None,
        *,
        prefer_gpu: bool = True,
        device_id: int = 0,
        engine_factory: Callable[[dict[str, Any]], object] | None = None,
        available_providers: Iterable[str] | None = None,
    ) -> None:
        self._engine = engine
        self._prefer_gpu = bool(prefer_gpu)
        self._device_id = int(device_id)
        self._engine_factory = engine_factory
        self._available_providers_override = (
            tuple(str(item) for item in available_providers)
            if available_providers is not None
            else None
        )
        self.runtime_info = OcrRuntimeInfo(
            requested_backend="custom" if engine is not None else ("gpu" if prefer_gpu else "cpu"),
            actual_provider="custom" if engine is not None else "not_initialized",
            device_id=self._device_id if prefer_gpu and engine is None else None,
        )

    @property
    def backend_label(self) -> str:
        info = self.runtime_info
        if not info.ocr_used:
            return "未使用 OCR"
        if info.actual_provider == "CUDAExecutionProvider":
            return f"OCR GPU（CUDAExecutionProvider，设备 {info.device_id or 0}）"
        if info.requested_backend == "custom":
            return "OCR 自定义引擎"
        if info.fallback_reason:
            return "OCR CPU（GPU 不可用或失败，已自动回退）"
        if info.actual_provider == "not_initialized":
            return "OCR 尚未初始化"
        return "OCR CPU（CPUExecutionProvider）"

    def _available_providers(self) -> tuple[str, ...]:
        if self._available_providers_override is not None:
            return self._available_providers_override
        try:
            import onnxruntime as ort

            return tuple(str(item) for item in ort.get_available_providers())
        except Exception as exc:
            LOGGER.warning("Unable to inspect ONNX Runtime providers: %s", exc)
            return ()

    @staticmethod
    def _preload_cuda_dependencies() -> None:
        """Load the unified PyTorch CUDA runtime, with legacy pip-wheel fallback."""
        global _OCR_CUDA_DLLS_PRELOADED
        if _OCR_CUDA_DLLS_PRELOADED:
            return
        with _OCR_CUDA_DLL_LOCK:
            if _OCR_CUDA_DLLS_PRELOADED:
                return
            try:
                import ctypes
                import os
                import site
                import onnxruntime as ort

                torch_dll_dir = _torch_cuda_dll_dir()
                preload_directory = ""
                if torch_dll_dir is not None:
                    preload_directory = str(torch_dll_dir)
                    if os.name == "nt" and hasattr(os, "add_dll_directory"):
                        _OCR_CUDA_DLL_HANDLES.append(os.add_dll_directory(str(torch_dll_dir)))
                    if os.name == "nt":
                        for library in _flat_cuda_dlls_in_load_order(torch_dll_dir):
                            _OCR_CUDA_DLL_HANDLES.append(ctypes.WinDLL(str(library)))
                else:
                    nvidia_roots = [Path(item) / "nvidia" for item in site.getsitepackages()]
                    for root in nvidia_roots:
                        if not root.is_dir():
                            continue
                        if os.name == "nt" and hasattr(os, "add_dll_directory"):
                            for directory in root.glob("*/bin"):
                                _OCR_CUDA_DLL_HANDLES.append(os.add_dll_directory(str(directory)))
                    if os.name == "nt":
                        for library in _nvidia_cuda_dlls_in_load_order(nvidia_roots):
                            _OCR_CUDA_DLL_HANDLES.append(ctypes.WinDLL(str(library)))
                preload = getattr(ort, "preload_dlls", None)
                if callable(preload):
                    preload(directory=preload_directory)
                _OCR_CUDA_DLLS_PRELOADED = True
            except Exception as exc:
                LOGGER.warning("Unable to preload ONNX Runtime CUDA dependencies: %s", exc)

    def prepare_runtime(self) -> None:
        """Prepare CUDA DLLs before Docling or RapidOCR creates ONNX sessions."""
        if self._prefer_gpu and "CUDAExecutionProvider" in self._available_providers():
            self._preload_cuda_dependencies()

    @staticmethod
    def _engine_providers(engine: object) -> tuple[str, ...]:
        providers: list[str] = []
        for component_name in ("text_det", "text_cls", "text_rec"):
            component = getattr(engine, component_name, None)
            session_wrapper = getattr(component, "session", None)
            session = getattr(session_wrapper, "session", session_wrapper)
            getter = getattr(session, "get_providers", None)
            if not callable(getter):
                continue
            try:
                values = tuple(str(item) for item in getter())
            except Exception:
                continue
            if values:
                providers.append(values[0])
        return tuple(providers)

    def _build_engine(self, *, use_cuda: bool) -> object:
        params: dict[str, Any] = {
            "EngineConfig.onnxruntime.use_cuda": bool(use_cuda),
        }
        if use_cuda:
            params["EngineConfig.onnxruntime.cuda_ep_cfg.device_id"] = self._device_id
        if self._engine_factory is not None:
            return self._engine_factory(params)
        try:
            from rapidocr import RapidOCR
        except ImportError as exc:
            raise DocumentExtractionError(
                "扫描 PDF 需要本地 OCR 依赖 rapidocr；请在 YOLO11-HAI 环境安装项目锁定依赖"
            ) from exc
        return RapidOCR(params=params)

    def _activate_cpu_fallback(self, reason: str) -> object:
        self.runtime_info.fallback_reason = str(reason)
        try:
            self._engine = self._build_engine(use_cuda=False)
        except DocumentExtractionError:
            raise
        except Exception as exc:
            raise DocumentExtractionError(
                f"本地 OCR CPU 回退初始化失败：{type(exc).__name__}: {exc}"
            ) from exc
        providers = self._engine_providers(self._engine)
        self.runtime_info.actual_provider = providers[0] if providers else "CPUExecutionProvider"
        self.runtime_info.device_id = None
        LOGGER.warning("RapidOCR is using CPU fallback: %s", reason)
        return self._engine

    def _get_engine(self) -> object:
        if self._engine is not None:
            return self._engine
        available = self._available_providers()
        use_cuda = self._prefer_gpu and "CUDAExecutionProvider" in available
        if self._prefer_gpu and not use_cuda:
            return self._activate_cpu_fallback("ONNX Runtime 未提供 CUDAExecutionProvider")
        if use_cuda and self._engine_factory is None:
            self.prepare_runtime()
        try:
            self._engine = self._build_engine(use_cuda=use_cuda)
        except Exception as exc:
            if use_cuda:
                return self._activate_cpu_fallback(
                    f"GPU 初始化失败：{type(exc).__name__}: {exc}"
                )
            raise DocumentExtractionError(
                f"本地 OCR 模型初始化失败：{type(exc).__name__}: {exc}"
            ) from exc
        providers = self._engine_providers(self._engine)
        if use_cuda and (not providers or any(item != "CUDAExecutionProvider" for item in providers)):
            observed = ", ".join(providers) if providers else "无法读取实际 provider"
            return self._activate_cpu_fallback(f"GPU session 未实际启用 CUDA：{observed}")
        self.runtime_info.actual_provider = (
            "CUDAExecutionProvider" if use_cuda else (providers[0] if providers else "CPUExecutionProvider")
        )
        self.runtime_info.device_id = self._device_id if use_cuda else None
        return self._engine

    def recognize(self, image: object) -> str:
        self.runtime_info.ocr_used = True
        try:
            output = self._get_engine()(image)
        except DocumentExtractionError:
            raise
        except Exception as exc:
            if self.runtime_info.actual_provider == "CUDAExecutionProvider":
                try:
                    output = self._activate_cpu_fallback(
                        f"GPU 推理失败：{type(exc).__name__}: {exc}"
                    )(image)
                except DocumentExtractionError:
                    raise
                except Exception as fallback_exc:
                    raise DocumentExtractionError(
                        f"本地 OCR GPU 推理失败，且 CPU 回退失败："
                        f"{type(fallback_exc).__name__}: {fallback_exc}"
                    ) from fallback_exc
            else:
                raise DocumentExtractionError(
                    f"本地 OCR 识别失败：{type(exc).__name__}: {exc}"
                ) from exc
        providers_after_inference = self._engine_providers(self._engine)
        if (
            self.runtime_info.actual_provider == "CUDAExecutionProvider"
            and providers_after_inference
            and any(item != "CUDAExecutionProvider" for item in providers_after_inference)
        ):
            self.runtime_info.fallback_reason = "GPU 推理期间由 ONNX Runtime 回退到 CPU"
            self.runtime_info.actual_provider = providers_after_inference[0]
            self.runtime_info.device_id = None
        def sequence(value: object | None) -> list[object]:
            if value is None:
                return []
            if hasattr(value, "tolist"):
                try:
                    value = value.tolist()
                except Exception:
                    pass
            if isinstance(value, (list, tuple)):
                return list(value)
            try:
                return list(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return []

        texts = getattr(output, "txts", None)
        if not sequence(texts):
            alternative = getattr(output, "texts", None)
            if alternative is not None:
                texts = alternative
        if isinstance(output, dict):
            for key in ("txts", "texts"):
                candidate = output.get(key)
                if not sequence(texts) and candidate is not None:
                    texts = candidate
        if not sequence(texts):
            word_results = output.get("word_results") if isinstance(output, dict) else getattr(output, "word_results", None)
            rows = sequence(word_results)
            if rows:
                texts = [values[0] for values in (sequence(item) for item in rows) if values and values[0] is not None]
        if not sequence(texts) and isinstance(output, (list, tuple)):
            rows = sequence(output)
            if len(rows) == 2 and isinstance(rows[0], (list, tuple)) and not isinstance(rows[1], (list, tuple, dict)):
                rows = sequence(rows[0])
            texts = [values[1] for values in (sequence(item) for item in rows) if len(values) >= 2 and values[1] is not None]
        text_values = sequence(texts)
        if not text_values:
            return ""
        return " ".join(str(text).strip() for text in text_values if str(text).strip()).strip()


def _has_usable_pdf_text(text: str) -> bool:
    return len(re.findall(r"[\w\u4e00-\u9fff]", str(text))) >= OCR_MIN_TEXT_CHARS


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _extract_docx(path: Path) -> list[tuple[str, str]]:
    try:
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist() if name == "word/document.xml" or name.startswith("word/header") or name.startswith("word/footer")]
            paragraphs: list[tuple[str, str]] = []
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            for name in sorted(names):
                root = ElementTree.fromstring(archive.read(name))
                for index, paragraph in enumerate(root.findall(".//w:p", ns), start=1):
                    text = "".join(node.text or "" for node in paragraph.findall(".//w:t", ns)).strip()
                    if text:
                        paragraphs.append((f"{name}:{index}", text))
            if not paragraphs:
                raise DocumentExtractionError("DOCX 中没有可提取的段落文本")
            return paragraphs
    except (zipfile.BadZipFile, OSError, ElementTree.ParseError) as exc:
        raise DocumentExtractionError(f"DOCX 解析失败：{type(exc).__name__}") from exc


def _native_pdf_pages(path: Path) -> list[tuple[int, str]]:
    try:
        from pypdf import PdfReader

        pages = PdfReader(str(path)).pages
        result: list[tuple[int, str]] = []
        for index, page in enumerate(pages, start=1):
            result.append((index, (page.extract_text() or "").strip()))
        return result
    except ImportError as exc:
        raise DocumentExtractionError("缺少 PDF 解析依赖 pypdf") from exc


def _pymupdf_native_pages(path: Path) -> list[tuple[int, str]]:
    try:
        import pymupdf

        with pymupdf.open(str(path)) as document:
            return [
                (index + 1, document[index].get_text("text").strip())
                for index in range(document.page_count)
            ]
    except ImportError as exc:
        raise DocumentExtractionError("缺少本地 PDF 渲染依赖 pymupdf") from exc
    except Exception as exc:
        raise DocumentExtractionError(f"PyMuPDF 原生文本解析失败：{type(exc).__name__}: {exc}") from exc


def _render_pdf_page(path: Path, page_number: int) -> object:
    try:
        import pymupdf
        import numpy as np

        document = pymupdf.open(str(path))
        try:
            if page_number < 1 or page_number > document.page_count:
                raise DocumentExtractionError(f"PDF 页码超出范围：{page_number}")
            matrix = pymupdf.Matrix(OCR_RENDER_SCALE, OCR_RENDER_SCALE)
            pixmap = document[page_number - 1].get_pixmap(matrix=matrix, alpha=False)
            return np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                pixmap.height, pixmap.width, pixmap.n
            )
        finally:
            document.close()
    except DocumentExtractionError:
        raise
    except ImportError as exc:
        raise DocumentExtractionError(
            "扫描 PDF 需要本地 PDF 渲染依赖 pymupdf；请在 YOLO11-HAI 环境安装项目锁定依赖"
        ) from exc
    except Exception as exc:
        raise DocumentExtractionError(
            f"PDF 页面渲染失败：第 {page_number} 页，{type(exc).__name__}: {exc}"
        ) from exc


def _extract_pdf(
    path: Path,
    *,
    ocr: LocalOcrAdapter | None = None,
    progress: Callable[[int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> list[tuple[str, str]]:
    native_pages: list[tuple[int, str]] = []
    native_error: Exception | None = None
    try:
        native_pages = _native_pdf_pages(path)
    except Exception as exc:
        native_error = exc

    if not native_pages:
        try:
            native_pages = _pymupdf_native_pages(path)
        except Exception as exc:
            detail = str(native_error or exc)
            raise DocumentExtractionError(f"PDF 打开失败：{type(exc).__name__}: {detail}") from exc

    ocr_adapter = ocr or LocalOcrAdapter()
    result: list[tuple[str, str]] = []
    total_pages = len(native_pages)
    for page_number, native_text in native_pages:
        if should_stop is not None and should_stop():
            raise DocumentExtractionCancelled("知识库导入已取消")
        if _has_usable_pdf_text(native_text):
            result.append((f"page:{page_number}", native_text.strip()))
        else:
            image = _render_pdf_page(path, page_number)
            recognized = ocr_adapter.recognize(image)
            if recognized:
                result.append((f"page:{page_number}", recognized))
        if progress is not None:
            progress(page_number, total_pages)
    if result:
        return result
    if native_error is not None:
        raise DocumentExtractionError(f"PDF 没有可提取的文字，且 OCR 无结果；原生解析错误：{native_error}")
    raise DocumentExtractionError("PDF 没有可提取的文字，且 OCR 无结果")


def extract_document(
    path: str | Path,
    *,
    ocr: LocalOcrAdapter | None = None,
    progress: Callable[[int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> list[tuple[str, str]]:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".docx":
        return _extract_docx(source)
    if suffix == ".pdf":
        return _extract_pdf(source, ocr=ocr, progress=progress, should_stop=should_stop)
    if suffix == ".doc":
        raise DocumentExtractionError("DOC 格式需要本机转换器；请先转换为 DOCX")
    raise DocumentExtractionError(f"不支持的文件类型：{suffix or '无扩展名'}")


def chunk_text(located_text: Iterable[tuple[str, str]], *, size: int = 1200, overlap: int = 160) -> list[tuple[str, str, str]]:
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("chunk size/overlap 无效")
    chunks: list[tuple[str, str, str]] = []
    for location, text in located_text:
        normalized = re.sub(r"\s+", " ", text).strip()
        start = 0
        part = 0
        while start < len(normalized):
            end = min(len(normalized), start + size)
            chunks.append((f"{location}#chunk:{part}", normalized[start:end], location))
            if end >= len(normalized):
                break
            start = end - overlap
            part += 1
    return chunks


class KnowledgeBase:
    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root_dir / "knowledge_base.sqlite3"
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _connect_readonly(self) -> sqlite3.Connection:
        connection = _readonly_sqlite(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    document_id TEXT PRIMARY KEY, path TEXT NOT NULL, name TEXT NOT NULL,
                    extension TEXT NOT NULL, size_bytes INTEGER NOT NULL, modified_ns INTEGER NOT NULL,
                    sha256 TEXT NOT NULL, status TEXT NOT NULL, folder_id TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS folders (
                    folder_id TEXT PRIMARY KEY, parent_id TEXT NOT NULL DEFAULT '', name TEXT NOT NULL,
                    relative_path TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, location TEXT NOT NULL,
                    text TEXT NOT NULL, FOREIGN KEY(document_id) REFERENCES documents(document_id) ON DELETE CASCADE
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(chunk_id UNINDEXED, document_id UNINDEXED, location UNINDEXED, text);
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(documents)").fetchall()}
            if "folder_id" not in columns:
                db.execute("ALTER TABLE documents ADD COLUMN folder_id TEXT NOT NULL DEFAULT ''")

    def create_folder(self, name: str, *, parent_id: str = "") -> KnowledgeBaseFolder:
        clean_name = re.sub(r"[\\/:*?\"<>|]+", "", str(name)).strip()
        if not clean_name or clean_name in {".", ".."}:
            raise ValueError("文件夹名称不能为空或包含非法字符")
        with self._connect() as db:
            parent_path = ""
            if parent_id:
                row = db.execute("SELECT relative_path FROM folders WHERE folder_id = ?", (parent_id,)).fetchone()
                if row is None:
                    raise ValueError("父级文件夹不存在")
                parent_path = str(row[0])
            relative_path = f"{parent_path}/{clean_name}" if parent_path else clean_name
            folder_id = hashlib.sha256(relative_path.encode()).hexdigest()[:20]
            db.execute("INSERT OR IGNORE INTO folders(folder_id, parent_id, name, relative_path) VALUES (?, ?, ?, ?)", (folder_id, parent_id, clean_name, relative_path))
            row = db.execute("SELECT * FROM folders WHERE folder_id = ?", (folder_id,)).fetchone()
        return KnowledgeBaseFolder(**dict(row))

    def list_folders(self) -> list[KnowledgeBaseFolder]:
        with closing(self._connect_readonly()) as db:
            rows = db.execute("SELECT * FROM folders ORDER BY relative_path COLLATE NOCASE").fetchall()
        return [KnowledgeBaseFolder(**dict(row)) for row in rows]

    def rename_folder(self, folder_id: str, name: str) -> KnowledgeBaseFolder:
        folder = next((item for item in self.list_folders() if item.folder_id == folder_id), None)
        if folder is None:
            raise ValueError("文件夹不存在")
        clean_name = re.sub(r"[\\/:*?\"<>|]+", "", str(name)).strip()
        if not clean_name or clean_name in {".", ".."}:
            raise ValueError("文件夹名称不能为空或包含非法字符")
        parent_path = ""
        if folder.parent_id:
            parent = next((item for item in self.list_folders() if item.folder_id == folder.parent_id), None)
            parent_path = parent.relative_path if parent else ""
        new_path = f"{parent_path}/{clean_name}" if parent_path else clean_name
        with self._connect() as db:
            conflict = db.execute("SELECT 1 FROM folders WHERE relative_path = ? AND folder_id != ?", (new_path, folder_id)).fetchone()
            if conflict:
                raise ValueError("同级文件夹已存在")
            old_path = folder.relative_path
            db.execute("UPDATE folders SET name = ?, relative_path = ? WHERE folder_id = ?", (clean_name, new_path, folder_id))
            descendants = db.execute("SELECT folder_id, relative_path FROM folders WHERE relative_path LIKE ?", (old_path + "/%",)).fetchall()
            for row in descendants:
                db.execute("UPDATE folders SET relative_path = ? WHERE folder_id = ?", (new_path + str(row[1])[len(old_path):], row[0]))
            row = db.execute("SELECT * FROM folders WHERE folder_id = ?", (folder_id,)).fetchone()
        return KnowledgeBaseFolder(**dict(row))

    def move_folder(self, folder_id: str, parent_id: str = "") -> KnowledgeBaseFolder:
        folder = next((item for item in self.list_folders() if item.folder_id == folder_id), None)
        if folder is None:
            raise ValueError("文件夹不存在")
        if parent_id == folder_id or parent_id in self.folder_descendants([folder_id]):
            raise ValueError("不能将文件夹移动到自身或其子文件夹内")
        parent = next((item for item in self.list_folders() if item.folder_id == parent_id), None) if parent_id else None
        parent_path = parent.relative_path if parent else ""
        new_path = f"{parent_path}/{folder.name}" if parent_path else folder.name
        with self._connect() as db:
            conflict = db.execute("SELECT 1 FROM folders WHERE relative_path = ? AND folder_id != ?", (new_path, folder_id)).fetchone()
            if conflict:
                raise ValueError("目标文件夹中已存在同名文件夹")
            old_path = folder.relative_path
            db.execute("UPDATE folders SET parent_id = ?, relative_path = ? WHERE folder_id = ?", (parent_id, new_path, folder_id))
            descendants = db.execute("SELECT folder_id, relative_path FROM folders WHERE relative_path LIKE ?", (old_path + "/%",)).fetchall()
            for row in descendants:
                db.execute("UPDATE folders SET relative_path = ? WHERE folder_id = ?", (new_path + str(row[1])[len(old_path):], row[0]))
            row = db.execute("SELECT * FROM folders WHERE folder_id = ?", (folder_id,)).fetchone()
        return KnowledgeBaseFolder(**dict(row))

    def delete_folder(self, folder_id: str, *, recursive: bool = True) -> list[str]:
        folder_ids = self.folder_descendants([folder_id]) if recursive else [folder_id]
        if not folder_ids:
            raise ValueError("文件夹不存在")
        placeholders = ",".join("?" for _ in folder_ids)
        with self._connect() as db:
            document_rows = db.execute(f"SELECT document_id FROM documents WHERE folder_id IN ({placeholders})", folder_ids).fetchall()
            document_ids = [str(row[0]) for row in document_rows]
            for document_id in document_ids:
                db.execute("DELETE FROM chunks_fts WHERE document_id = ?", (document_id,))
                db.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
                db.execute("DELETE FROM documents WHERE document_id = ?", (document_id,))
            db.execute(f"DELETE FROM folders WHERE folder_id IN ({placeholders})", folder_ids)
        return document_ids

    def folder_descendants(self, folder_ids: Iterable[str]) -> list[str]:
        selected = set(str(item) for item in folder_ids if item)
        if not selected:
            return []
        folders = self.list_folders()
        paths = {folder.folder_id: folder.relative_path for folder in folders}
        prefixes = [paths[item] for item in selected if item in paths]
        return [folder_id for folder_id, path in paths.items() if any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes)]

    @staticmethod
    def _document_id(path: Path, digest: str) -> str:
        return hashlib.sha256(f"{path.resolve()}:{digest}".encode()).hexdigest()[:20]

    def _copy_to_managed_files(self, source: Path, digest: str) -> Path:
        """Return a stable project-owned copy without modifying the selected source."""
        managed_dir = (self.root_dir / "source_files").resolve()
        managed_dir.mkdir(parents=True, exist_ok=True)
        try:
            if source.is_relative_to(managed_dir):
                return source
        except AttributeError:  # pragma: no cover - Python < 3.9 compatibility
            if str(source).startswith(str(managed_dir)):
                return source
        target = managed_dir / source.name
        if target.exists() and _sha256(target) != digest:
            target = managed_dir / f"{source.stem}_{digest[:12]}{source.suffix.lower()}"
        if not target.exists():
            try:
                shutil.copy2(source, target)
            except OSError as exc:
                raise DocumentExtractionError(f"知识库文件复制失败：{type(exc).__name__}") from exc
        return target

    def add_document(
        self,
        path: str | Path,
        *,
        folder_id: str = "",
        chunk_size: int = 1200,
        chunk_overlap: int = 160,
        ocr: LocalOcrAdapter | None = None,
        progress: Callable[[int, int], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> DocumentRecord:
        source = Path(path).resolve()
        if source.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise DocumentExtractionError(f"不支持的文件类型：{source.suffix or '无扩展名'}")
        if should_stop is not None and should_stop():
            raise DocumentExtractionCancelled("知识库导入已取消")
        digest = _sha256(source)
        source = self._copy_to_managed_files(source, digest)
        stat = source.stat()
        document_id = self._document_id(source, digest)
        record = DocumentRecord(document_id, str(source), source.name, source.suffix.lower(), stat.st_size, stat.st_mtime_ns, digest, "indexing", folder_id=folder_id)
        with self._connect() as db:
            existing_row = db.execute("SELECT * FROM documents WHERE document_id = ?", (document_id,)).fetchone()
            if existing_row is not None and str(existing_row["status"]) == "ready":
                if str(existing_row["folder_id"]) != folder_id:
                    db.execute("UPDATE documents SET folder_id = ?, error = '' WHERE document_id = ?", (folder_id, document_id))
                    existing_row = db.execute("SELECT * FROM documents WHERE document_id = ?", (document_id,)).fetchone()
                return DocumentRecord(**dict(existing_row))
        try:
            located = extract_document(source, ocr=ocr, progress=progress, should_stop=should_stop)
            chunks = chunk_text(located, size=chunk_size, overlap=chunk_overlap)
            if should_stop is not None and should_stop():
                raise DocumentExtractionCancelled("知识库导入已取消")
        except DocumentExtractionCancelled:
            raise
        except Exception as exc:
            record.status = "failed"
            record.error = str(exc)
            with self._connect() as db:
                db.execute("INSERT OR REPLACE INTO documents(document_id, path, name, extension, size_bytes, modified_ns, sha256, status, folder_id, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", tuple(asdict(record).values()))
                db.execute("DELETE FROM chunks_fts WHERE document_id = ?", (document_id,))
                db.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
            return record
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO documents(document_id, path, name, extension, size_bytes, modified_ns, sha256, status, folder_id, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", tuple(asdict(record).values()))
            db.execute("DELETE FROM chunks_fts WHERE document_id = ?", (document_id,))
            db.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
            for index, (location, text, source_location) in enumerate(chunks):
                chunk_id = f"{document_id}:{index}"
                db.execute("INSERT INTO chunks VALUES (?, ?, ?, ?)", (chunk_id, document_id, source_location, text))
                db.execute("INSERT INTO chunks_fts VALUES (?, ?, ?, ?)", (chunk_id, document_id, source_location, text))
            record.status = "ready"
            db.execute("UPDATE documents SET status = ?, error = '' WHERE document_id = ?", (record.status, document_id))
        return record

    def register_ready_document(
        self,
        *,
        document_id: str,
        path: str | Path,
        sha256: str,
        folder_id: str = "",
    ) -> DocumentRecord:
        """Register an already-active source without repeating legacy extraction."""
        source = Path(path).resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        stat = source.stat()
        record = DocumentRecord(
            str(document_id), str(source), source.name, source.suffix.lower(),
            int(stat.st_size), int(stat.st_mtime_ns), str(sha256), "ready",
            folder_id=str(folder_id), error="",
        )
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO documents(document_id, path, name, extension, size_bytes, modified_ns, sha256, status, folder_id, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(asdict(record).values()),
            )
        return record

    def list_documents(self) -> list[DocumentRecord]:
        with closing(self._connect_readonly()) as db:
            rows = db.execute("SELECT * FROM documents ORDER BY name COLLATE NOCASE").fetchall()
        return [DocumentRecord(**dict(row)) for row in rows]

    def list_documents_in_folders(self, folder_ids: Iterable[str] | None = None) -> list[DocumentRecord]:
        expanded = self.folder_descendants(folder_ids or [])
        if not expanded:
            return self.list_documents()
        placeholders = ",".join("?" for _ in expanded)
        with closing(self._connect_readonly()) as db:
            rows = db.execute(f"SELECT * FROM documents WHERE folder_id IN ({placeholders}) ORDER BY name COLLATE NOCASE", expanded).fetchall()
        return [DocumentRecord(**dict(row)) for row in rows]

    def document_ids_for_folders(self, folder_ids: Iterable[str] | None = None) -> list[str]:
        """Resolve selected folders recursively to ready document IDs."""
        expanded = self.folder_descendants(folder_ids or [])
        if not expanded:
            return []
        placeholders = ",".join("?" for _ in expanded)
        with closing(self._connect_readonly()) as db:
            rows = db.execute(
                f"SELECT document_id FROM documents WHERE status = 'ready' AND folder_id IN ({placeholders}) ORDER BY name COLLATE NOCASE",
                expanded,
            ).fetchall()
        return [str(row[0]) for row in rows if str(row[0])]

    def remove_document(self, document_id: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM chunks_fts WHERE document_id = ?", (document_id,))
            db.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
            db.execute("DELETE FROM documents WHERE document_id = ?", (document_id,))

    def search(self, query: str, *, document_ids: Iterable[str] | None = None, folder_ids: Iterable[str] | None = None, top_k: int = 6, max_chars: int = 10000) -> list[KnowledgeBaseChunk]:
        tokens = _search_tokens(query)
        ids = list(document_ids or [])
        if folder_ids:
            folder_document_ids = [record.document_id for record in self.list_documents_in_folders(folder_ids)]
            ids = sorted(set(ids).intersection(folder_document_ids)) if ids else folder_document_ids
            if not ids:
                return []
        with closing(self._connect_readonly()) as db:
            if ids:
                placeholders = ",".join("?" for _ in ids)
                chunk_count = int(db.execute(f"SELECT COUNT(*) FROM chunks WHERE document_id IN ({placeholders})", ids).fetchone()[0])
            else:
                chunk_count = int(db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
        if chunk_count == 0:
            return []
        title_query = _normalize_source_title(query)
        if len(title_query) >= 4:
            title_params: list[object] = []
            title_where = ""
            if ids:
                title_where = f" AND d.document_id IN ({','.join('?' for _ in ids)})"
                title_params.extend(ids)
            with closing(self._connect_readonly()) as db:
                title_rows = db.execute(
                    f"SELECT c.chunk_id, c.document_id, c.location, c.text, 0.0 AS score, d.name "
                    f"FROM chunks c JOIN documents d ON d.document_id=c.document_id "
                    f"WHERE d.status='ready'{title_where} ORDER BY d.name COLLATE NOCASE, c.location COLLATE NOCASE, c.chunk_id",
                    title_params,
                ).fetchall()
            exact = [row for row in title_rows if _normalize_source_title(row["name"]) == title_query]
            matched = exact or [row for row in title_rows if (
                (source_title := _normalize_source_title(row["name"]))
                and (title_query in source_title or (len(source_title) >= 4 and source_title in title_query))
            )]
            if matched:
                return self._bounded_chunks(matched, top_k=top_k, max_chars=max_chars)
        if not tokens:
            return []
        fts_query = " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)
        params: list[object] = [fts_query]
        where = ""
        if ids:
            where = f" AND c.document_id IN ({','.join('?' for _ in ids)})"
            params.extend(ids)
        params.append(max(1, int(top_k)))
        sql = f"SELECT c.chunk_id, c.document_id, c.location, c.text, bm25(chunks_fts) AS score FROM chunks_fts JOIN chunks c USING(chunk_id) WHERE chunks_fts MATCH ?{where} ORDER BY score LIMIT ?"
        try:
            with closing(self._connect_readonly()) as db:
                rows = db.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            rows = []
        if not rows:
            like = " OR ".join("c.text LIKE ?" for _ in tokens)
            params_like: list[object] = [f"%{token}%" for token in tokens]
            if ids:
                like += f" AND c.document_id IN ({','.join('?' for _ in ids)})"
                params_like.extend(ids)
            params_like.append(max(1, int(top_k)))
            with closing(self._connect_readonly()) as db:
                rows = db.execute(f"SELECT c.chunk_id, c.document_id, c.location, c.text, 0.0 AS score FROM chunks c WHERE ({like}) LIMIT ?", params_like).fetchall()
        results: list[KnowledgeBaseChunk] = []
        used = 0
        for row in rows:
            text = str(row["text"])
            if used + len(text) > max_chars:
                text = text[: max(0, max_chars - used)]
            if not text:
                break
            results.append(KnowledgeBaseChunk(str(row["chunk_id"]), str(row["document_id"]), str(row["location"]), text, float(row["score"])))
            used += len(text)
        return results

    def _resolve_scope_document_ids(
        self,
        *,
        document_ids: Iterable[str] | None = None,
        folder_ids: Iterable[str] | None = None,
    ) -> list[str]:
        """Resolve the selected profile scope without broadening it."""
        requested_documents = {str(item) for item in (document_ids or []) if str(item)}
        if folder_ids:
            expanded_folder_ids = self.folder_descendants(folder_ids)
            folder_document_ids = (
                {
                    record.document_id
                    for record in self.list_documents_in_folders(expanded_folder_ids)
                }
                if expanded_folder_ids
                else set()
            )
            if requested_documents:
                requested_documents.intersection_update(folder_document_ids)
            else:
                requested_documents = folder_document_ids
        if requested_documents:
            return sorted(requested_documents)
        if folder_ids:
            return []
        with closing(self._connect_readonly()) as db:
            rows = db.execute(
                "SELECT document_id FROM documents WHERE status = 'ready' ORDER BY name COLLATE NOCASE, document_id"
            ).fetchall()
        return [str(row[0]) for row in rows]

    @staticmethod
    def _bounded_chunks(rows: Iterable[sqlite3.Row], *, top_k: int, max_chars: int) -> list[KnowledgeBaseChunk]:
        results: list[KnowledgeBaseChunk] = []
        used = 0
        for row in rows:
            text = str(row["text"])
            if used + len(text) > max_chars:
                text = text[: max(0, max_chars - used)]
            if not text:
                break
            results.append(
                KnowledgeBaseChunk(
                    str(row["chunk_id"]),
                    str(row["document_id"]),
                    str(row["location"]),
                    text,
                    float(row["score"]),
                )
            )
            used += len(text)
            if len(results) >= max(1, int(top_k)):
                break
        return results

    def search_with_scope(
        self,
        query: str,
        *,
        document_ids: Iterable[str] | None = None,
        folder_ids: Iterable[str] | None = None,
        top_k: int = 6,
        max_chars: int = 10000,
    ) -> KnowledgeBaseSearchResult:
        """Search a profile scope and deterministically fall back within that scope.

        A profile with ready indexed material must not silently become an
        unreferenced generation. Lexical search remains preferred; a miss uses
        the first chunks ordered by document/location/id, never another scope.
        """
        scope_ids = self._resolve_scope_document_ids(
            document_ids=document_ids,
            folder_ids=folder_ids,
        )
        lexical = self.search(
            query,
            document_ids=scope_ids,
            top_k=top_k,
            max_chars=max_chars,
        ) if scope_ids else []
        if lexical:
            return KnowledgeBaseSearchResult(
                tuple(lexical),
                scope_available=True,
                scope_document_ids=tuple(scope_ids),
                retrieval_mode="lexical",
            )
        if not scope_ids:
            return KnowledgeBaseSearchResult((), scope_available=False)
        placeholders = ",".join("?" for _ in scope_ids)
        with closing(self._connect_readonly()) as db:
            rows = db.execute(
                f"""SELECT c.chunk_id, c.document_id, c.location, c.text, 0.0 AS score
                    FROM chunks c JOIN documents d ON d.document_id = c.document_id
                    WHERE d.status = 'ready' AND c.document_id IN ({placeholders})
                    ORDER BY d.name COLLATE NOCASE, c.location COLLATE NOCASE, c.chunk_id
                    LIMIT ?""",
                [*scope_ids, max(1, int(top_k))],
            ).fetchall()
        fallback = self._bounded_chunks(rows, top_k=top_k, max_chars=max_chars)
        return KnowledgeBaseSearchResult(
            tuple(fallback),
            scope_available=bool(fallback),
            used_scoped_fallback=bool(fallback),
            scope_document_ids=tuple(scope_ids),
            retrieval_mode="scoped_fallback",
        )


# Compatibility facade for the extracted stage scripts.  Existing callers keep
# using this module, while new modules can call the independent pipeline
# directly.  Imports are intentionally lazy at call time because
# knowledge_pipeline.pdf_convert reuses this legacy module's native/OCR
# adapters.
def convert_document_pipeline(path: str | Path, **kwargs: object):
    from knowledge_pipeline.pdf_convert import convert_document

    return convert_document(path, **kwargs)


def chunk_conversion_pipeline(conversion: object, **kwargs: object):
    from knowledge_pipeline.chunk import chunk_conversion

    return chunk_conversion(conversion, **kwargs)


def retrieve_pipeline(query: str, db_path: str | Path, **kwargs: object):
    from knowledge_pipeline.retrieve import retrieve

    return retrieve(query, db_path, **kwargs)


def pipeline_search_with_scope(
    kb: KnowledgeBase,
    query: str,
    *,
    document_ids: Iterable[str] | None = None,
    folder_ids: Iterable[str] | None = None,
    top_k: int = 6,
    max_chars: int = 10000,
) -> KnowledgeBaseSearchResult:
    """Use the active production v2 retrieval stage exclusively.

    The legacy catalog is still allowed for resolving GUI folder/document
    selections, but it is never queried for retrieval.  This prevents mixed
    v1/v2 answers and makes an invalid active manifest fail fast.
    """
    # Preserve test doubles and third-party adapters that implement the old
    # public method without exposing KnowledgeBase internals.
    if not hasattr(kb, "_resolve_scope_document_ids"):
        return kb.search_with_scope(  # type: ignore[attr-defined]
            query,
            document_ids=document_ids,
            folder_ids=folder_ids,
            top_k=top_k,
            max_chars=max_chars,
        )
    requested_document_ids = tuple(str(item) for item in (document_ids or []) if str(item))
    requested_folder_ids = tuple(str(item) for item in (folder_ids or []) if str(item))
    scope_ids = kb._resolve_scope_document_ids(
        document_ids=requested_document_ids,
        folder_ids=requested_folder_ids,
    )
    scope_was_selected = bool(requested_document_ids) or bool(requested_folder_ids)
    if scope_was_selected and not scope_ids:
        return KnowledgeBaseSearchResult(
            (),
            scope_available=False,
            scope_document_ids=(),
            retrieval_mode="none",
            warnings=("selected scope has no explicit active-v2 document IDs; unrelated documents were not searched",),
        )

    v2_result = None
    v2_scope_ids: list[str] = []
    missing_scope_ids: list[str] = []
    active, semantic, runtime_warnings = _active_runtime_state()
    pipeline_warnings: list[str] = list(runtime_warnings)
    if not active.get("active"):
        reason = str(active.get("reason") or "active production RAG is unavailable")
        raise RuntimeError(f"active production RAG unavailable: {reason}")
    try:
        from knowledge_pipeline.retrieve import retrieve

        database_path = Path(str(active["database"]["path"]))
        with closing(_readonly_sqlite(database_path)) as db:
            if scope_ids:
                placeholders = ",".join("?" for _ in scope_ids)
                rows = db.execute(
                    f"""SELECT DISTINCT document_id
                        FROM pipeline_chunks
                        WHERE retrieval_role = 'retrieval'
                          AND document_id IN ({placeholders})""",
                    scope_ids,
                ).fetchall()
                available_ids = {str(row[0]) for row in rows}
                v2_scope_ids = [item for item in scope_ids if item in available_ids]
                missing_scope_ids = [item for item in scope_ids if item not in available_ids]
            else:
                v2_scope_ids = []
                missing_scope_ids = []

            if missing_scope_ids:
                # A frozen release may have a stale, but otherwise healthy,
                # user overlay from an older installation. If the current
                # GUI scope belongs to the immutable bundled snapshot, use
                # that read-only baseline for this operation only.
                bundled_manifest = None
                try:
                    import sys
                    from runtime.app_paths import application_resource_root

                    candidate = application_resource_root() / "knowledge_base" / "active_rag.json"
                    if bool(getattr(sys, "frozen", False)) and candidate.is_file():
                        bundled_manifest = candidate.resolve()
                except (OSError, TypeError, ValueError):
                    bundled_manifest = None
                current_manifest = Path(str(active.get("manifest_path") or "")).resolve()
                if bundled_manifest is not None and bundled_manifest != current_manifest:
                    bundled_active, bundled_semantic, bundled_warnings = _active_runtime_state(bundled_manifest)
                    bundled_database = Path(str((bundled_active.get("database") or {}).get("path") or ""))
                    bundled_available: set[str] = set()
                    if bundled_active.get("active") and bundled_database.is_file():
                        with closing(_readonly_sqlite(bundled_database)) as bundled_db:
                            placeholders = ",".join("?" for _ in scope_ids)
                            rows = bundled_db.execute(
                                f"""SELECT DISTINCT document_id FROM pipeline_chunks
                                    WHERE retrieval_role = 'retrieval' AND document_id IN ({placeholders})""",
                                scope_ids,
                            ).fetchall()
                            bundled_available = {str(row[0]) for row in rows}
                    if bundled_available == set(scope_ids):
                        active, semantic = bundled_active, bundled_semantic
                        pipeline_warnings.extend(bundled_warnings)
                        pipeline_warnings.append("bundled_active_rag_selected_for_scope_compatibility")
                        database_path = bundled_database
                        v2_scope_ids = list(scope_ids)
                        missing_scope_ids = []
                if missing_scope_ids:
                    raise RuntimeError(
                        "selected active-v2 document IDs are unavailable: " + ", ".join(missing_scope_ids)
                    )

        if v2_scope_ids or (not scope_was_selected and not scope_ids):
            candidate_top_k = max(30, max(1, int(top_k)) * 5)
            candidate_max_chars = max(int(max_chars), min(100_000, candidate_top_k * 4_000))
            v2_result = retrieve(
                query,
                database_path,
                document_ids=v2_scope_ids,
                top_k=candidate_top_k,
                max_chars=candidate_max_chars,
                semantic_retriever=semantic,
                expansion_mode="forced",
                routing_mode="adaptive",
            )
    except RuntimeError:
        raise
    except Exception as exc:
        LOGGER.exception("Active v2 retrieval failed")
        raise RuntimeError(f"active production RAG retrieval failed: {type(exc).__name__}") from exc

    if v2_result is None:
        raise RuntimeError("active production RAG returned no result for the selected scope")
    raw_chunks = list(v2_result.chunks)
    result = v2_result
    retrieval_mode = v2_result.retrieval_mode
    used_scoped_fallback = v2_result.retrieval_mode == "scoped_fallback"
    scope_available = v2_result.scope_available

    from knowledge_pipeline.rerank import rerank_payload

    direct_chunks = [
        chunk for chunk in raw_chunks
        if not bool((chunk.metadata or {}).get("expanded"))
        and str((chunk.metadata or {}).get("retrieval_role") or "retrieval") == "retrieval"
    ]
    rerank_input = {
        "query": query,
        "chunks": [
            {
                "chunk_id": chunk.chunk_id,
                "document_id": chunk.document_id,
                "location": chunk.location,
                "text": chunk.text,
                "parent_id": chunk.parent_id,
                "source_marker": chunk.source_marker,
                "metadata": dict(chunk.metadata or {}),
            }
            for chunk in direct_chunks
        ],
        "retrieval_mode": v2_result.retrieval_mode,
        "relevance_status": v2_result.relevance_status,
        "scope_available": v2_result.scope_available,
        "scope_document_ids": list(v2_result.scope_document_ids),
        "status": v2_result.status.to_dict(),
        "route_diagnostics": dict(v2_result.route_diagnostics or {}),
    }
    reranked = rerank_payload(rerank_input, top_k=max(1, int(top_k)))
    by_id = {str(chunk.chunk_id): chunk for chunk in raw_chunks}
    selected_anchor_ids = [str(item.get("chunk_id") or "") for item in reranked.chunks if item.get("chunk_id")]
    selected_anchor_set = set(selected_anchor_ids)
    selected_groups: list[dict[str, Any]] = []
    context_ids: list[str] = []
    context_anchor_map: dict[str, str] = {}
    for group in v2_result.context_groups:
        group_anchor_ids = {str(item) for item in (group.get("anchor_chunk_ids") or []) if str(item)}
        if not (group_anchor_ids & selected_anchor_set):
            continue
        copied = dict(group)
        copied["anchor_chunk_ids"] = [item for item in (group.get("anchor_chunk_ids") or []) if str(item) in selected_anchor_set]
        selected_groups.append(copied)
        for chunk_id in group.get("chunk_ids") or []:
            value = str(chunk_id)
            if value and value not in selected_anchor_set and value not in context_ids:
                context_ids.append(value)
                context_anchor_map[value] = str(copied["anchor_chunk_ids"][0]) if copied["anchor_chunk_ids"] else ""
    ordered_chunks = [by_id[item] for item in selected_anchor_ids if item in by_id]
    for item in context_ids:
        if item not in by_id:
            continue
        original = by_id[item]
        context_metadata = dict(original.metadata or {})
        context_metadata.update({
            "expanded": True,
            "anchor": False,
            "anchor_chunk_id": context_anchor_map.get(item, ""),
            "expansion_reason": context_metadata.get("expansion_reason") or "selected_anchor_context",
        })
        ordered_chunks.append(
            type(original)(
                original.chunk_id,
                original.document_id,
                original.location,
                original.text,
                original.parent_id,
                original.source_marker,
                context_metadata,
            )
        )
    selected_anchors = [
        dict(item) for item in v2_result.anchors
        if str(item.get("chunk_id") or "") in selected_anchor_set
    ]

    chunks: list[KnowledgeBaseChunk] = []
    used_chars = 0
    seen_chunks: set[tuple[str, str]] = set()
    for chunk in ordered_chunks:
        key = (str(chunk.document_id), str(chunk.chunk_id))
        if key in seen_chunks:
            continue
        remaining = max(0, int(max_chars) - used_chars)
        text = str(chunk.text)[:remaining]
        if not text:
            break
        chunks.append(
            KnowledgeBaseChunk(
                chunk.chunk_id,
                chunk.document_id,
                chunk.location,
                text,
                float((chunk.metadata or {}).get("score", 0.0)),
                {
                    **dict(chunk.metadata or {}),
                    **(
                        {
                            "rerank_score": next(
                                (float(item.get("rerank_score", 0.0)) for item in reranked.chunks if str(item.get("chunk_id")) == str(chunk.chunk_id)),
                                0.0,
                            ),
                            "reranker": reranked.reranker,
                        }
                        if str(chunk.chunk_id) in selected_anchor_set else {}
                    ),
                },
                str(chunk.source_marker or ""),
            )
        )
        seen_chunks.add(key)
        used_chars += len(text)

    status_warnings: list[str] = []
    status_warnings.extend(str(item) for item in (v2_result.status.warnings or []) if str(item))
    return KnowledgeBaseSearchResult(
        tuple(chunks),
        scope_available=scope_available,
        used_scoped_fallback=used_scoped_fallback,
        scope_document_ids=tuple(scope_ids),
        anchors=tuple(selected_anchors),
        context_groups=tuple(selected_groups),
        retrieval_mode=retrieval_mode,
        warnings=tuple(dict.fromkeys([*pipeline_warnings, *status_warnings])),
        route_diagnostics={
            **dict(getattr(result, "route_diagnostics", {}) or {}),
            "rerank_enabled": True,
            "reranker": reranked.reranker,
            "rerank_stage_version": reranked.status.stage_version,
            "rerank_candidate_count": len(direct_chunks),
            "rerank_selected_count": len(selected_anchor_ids),
            "candidate_pool_size": max(30, max(1, int(top_k)) * 5),
        },
        reranker=reranked.reranker,
        rerank_status=reranked.status.status,
        rerank_candidate_count=len(direct_chunks),
        rerank_selected_count=len(selected_anchor_ids),
    )


def rerank_pipeline(payload: dict[str, object], **kwargs: object):
    from knowledge_pipeline.rerank import rerank_payload

    return rerank_payload(payload, **kwargs)


def build_generation_context_pipeline(**kwargs: object):
    from knowledge_pipeline.generate import build_generation_context

    return build_generation_context(**kwargs)
