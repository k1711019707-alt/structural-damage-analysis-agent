from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from runtime.app_paths import application_resource_root


FHL_PROMPT_VERSION = "repair-render.v1"
FHL_IMAGE_MODEL = "gpt-image-2"
FHL_EDIT_SIZE = "2048x1536"
FHL_MAX_ATTEMPTS = 4
FHL_RETRYABLE_STATUSES = frozenset({429, 502, 503, 504, 524})
_MAX_IMAGE_BYTES = 50 * 1024 * 1024
_MAX_API_RESPONSE_BYTES = 70 * 1024 * 1024


def resolve_default_fhl_script() -> Path:
    explicit = os.environ.get("FHL_IMAGE_GEN_SCRIPT", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    bundled = application_resource_root() / "fhl_plugin" / "generate.mjs"
    if bundled.is_file():
        return bundled
    project_owned = Path(__file__).resolve().parents[1] / "packaging" / "fhl_plugin" / "generate.mjs"
    if project_owned.is_file():
        return project_owned
    codex_root = Path(os.environ.get("CODEX_HOME", "").strip() or (Path.home() / ".codex"))
    candidates = sorted(
        codex_root.glob("plugins/cache/fhl-plugins/fhl-image-gen/*/scripts/generate.mjs"),
        reverse=True,
    )
    return candidates[0] if candidates else project_owned


def resolve_default_node_executable() -> str:
    explicit = os.environ.get("FHL_NODE_EXE", "").strip()
    if explicit:
        return str(Path(explicit).expanduser())
    bundled = application_resource_root() / "fhl_plugin" / "node.exe"
    if bundled.is_file():
        return str(bundled)
    return shutil.which("node") or shutil.which("node.exe") or "node"


DEFAULT_FHL_SCRIPT = resolve_default_fhl_script()


@dataclass(frozen=True)
class RenderResult:
    source_path: str
    output_path: str | None
    status: str
    message: str
    prompt_version: str = FHL_PROMPT_VERSION


class FhlRepairRenderer:
    def __init__(
        self,
        *,
        script_path: str | Path = DEFAULT_FHL_SCRIPT,
        output_dir: str | Path,
        api_key: str | None = None,
        api_url: str | None = None,
        base_prompt: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        node_executable: str | None = None,
        opener: Callable[..., object] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.script_path = Path(script_path)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.node_executable = node_executable or resolve_default_node_executable()
        self.api_key = str(api_key or "").strip()
        self.api_url = str(api_url or "").strip()
        self.base_prompt = (base_prompt or "").strip()
        self.runner = runner or subprocess.run
        self.opener = opener or urlopen
        self.sleep = sleep or time.sleep
        self.manifest_path = self.output_dir / "render_manifest.json"
        self.manifest: list[dict[str, object]] = self._load_manifest()

    def _failure_message(self, result: subprocess.CompletedProcess[str]) -> str:
        return_code = getattr(result, "returncode", "unknown")
        output_parts = [
            str(value)
            for value in (getattr(result, "stderr", ""), getattr(result, "stdout", ""))
            if value
        ]
        lines = [
            re.sub(r"\s+", " ", line).strip()
            for line in "\n".join(output_parts).splitlines()
            if line.strip()
        ]
        preferred = [
            line
            for line in lines
            if any(marker in line.upper() for marker in ("EDIT FAILED:", "HTTP", "ERROR", "FAILED"))
        ]
        diagnostic = (preferred or lines)[-1] if lines else ""
        if self.api_key:
            diagnostic = diagnostic.replace(self.api_key, "<redacted>")
        diagnostic = diagnostic[:400].rstrip()
        message = f"FHL Images API 未成功回傳（exit {return_code}）"
        return f"{message}：{diagnostic}" if diagnostic else message

    def _load_manifest(self) -> list[dict[str, object]]:
        if not self.manifest_path.is_file():
            return []
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            return list(data.get("renders", []))
        except (OSError, json.JSONDecodeError):
            return []

    def _save_manifest(self) -> None:
        payload = json.dumps(
            {"prompt_version": FHL_PROMPT_VERSION, "renders": self.manifest},
            ensure_ascii=False,
            indent=2,
        )
        temporary = self.manifest_path.with_name(f".{self.manifest_path.name}.tmp")
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, self.manifest_path)

    def _output_path(self, source_path: Path) -> Path:
        return self.output_dir / f"{source_path.stem}__修復渲染{source_path.suffix.lower()}"

    def _direct_output_path(self, source_path: Path) -> Path:
        return self.output_dir / f"{source_path.stem}__修復渲染.png"

    def _safe_text(self, value: object, *, limit: int = 500) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if self.api_key:
            text = text.replace(self.api_key, "<redacted>")
        return text[:limit].rstrip()

    def _endpoint(self) -> str:
        raw = self.api_url.rstrip("/")
        parsed = urlsplit(raw)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("FHL Image Gen API URL 无效")
        path = parsed.path.rstrip("/")
        if not path:
            path = "/v1/images/edits"
        elif path.endswith("/v1"):
            path = f"{path}/images/edits"
        elif not path.endswith("/images/edits"):
            raise ValueError("FHL Image Gen API URL 必须是服务根地址、/v1 或 /v1/images/edits")
        return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))

    @staticmethod
    def _multipart_field(boundary: str, name: str, value: str) -> bytes:
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode("utf-8")

    def _multipart_body(self, source: Path, prompt: str) -> tuple[bytes, str]:
        boundary = f"yolo11-fhl-{uuid.uuid4().hex}"
        mime_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        filename = source.name.replace('"', "_").replace("\r", "_").replace("\n", "_")
        parts = [
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'
                f"Content-Type: {mime_type}\r\n\r\n"
            ).encode("utf-8"),
            source.read_bytes(),
            b"\r\n",
        ]
        for name, value in (
            ("prompt", prompt),
            ("model", FHL_IMAGE_MODEL),
            ("n", "1"),
            ("size", FHL_EDIT_SIZE),
            ("quality", "auto"),
            ("output_format", "png"),
            ("response_format", "b64_json"),
        ):
            parts.append(self._multipart_field(boundary, name, value))
        parts.append(f"--{boundary}--\r\n".encode("ascii"))
        return b"".join(parts), boundary

    @staticmethod
    def _read_response(response: object, *, limit: int) -> bytes:
        try:
            body = response.read(limit + 1)  # type: ignore[attr-defined]
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        if len(body) > limit:
            raise ValueError("FHL 响应数据超过允许大小")
        return body

    def _http_failure(self, exc: HTTPError) -> str:
        try:
            body = exc.read(_MAX_API_RESPONSE_BYTES + 1)
        except OSError:
            body = b""
        detail: object = ""
        if body:
            try:
                payload = json.loads(body.decode("utf-8", errors="replace"))
                if isinstance(payload, dict):
                    error = payload.get("error")
                    if isinstance(error, dict):
                        detail = error.get("message") or error.get("type") or error
                    else:
                        detail = payload.get("message") or error or payload
                else:
                    detail = payload
            except json.JSONDecodeError:
                detail = body.decode("utf-8", errors="replace")
        message = f"FHL Images API 请求失败（HTTP {exc.code}）"
        safe_detail = self._safe_text(detail)
        return f"{message}：{safe_detail}" if safe_detail else message

    @staticmethod
    def _retry_delay(exc: HTTPError) -> float:
        retry_after = ""
        headers = getattr(exc, "headers", None)
        if headers is not None:
            retry_after = str(headers.get("Retry-After", "")).strip()
        try:
            return min(15.0, max(0.0, float(retry_after)))
        except ValueError:
            return 15.0

    @staticmethod
    def _extract_image(payload: object) -> bytes:
        if not isinstance(payload, dict):
            raise ValueError("FHL 返回格式不是 JSON 对象")
        items = payload.get("data")
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            raise ValueError("FHL 响应中没有可用的 base64 图片")
        item = items[0]
        nested = item.get("image")
        encoded = item.get("b64_json") or item.get("base64")
        if not encoded and isinstance(nested, dict):
            encoded = nested.get("b64_json")
        if not isinstance(encoded, str) or not encoded.strip():
            raise ValueError("FHL 响应中没有可用的 base64 图片")
        try:
            image = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("FHL 返回的 base64 图片无效") from exc
        if not image:
            raise ValueError("FHL 返回的图片为空")
        if len(image) > _MAX_IMAGE_BYTES:
            raise ValueError("FHL 返回的图片超过允许大小")
        return image

    def _render_with_gui_api(self, source: Path, prompt: str) -> RenderResult:
        try:
            endpoint = self._endpoint()
            body, boundary = self._multipart_body(source, prompt)
        except (OSError, ValueError) as exc:
            return RenderResult(
                str(source),
                None,
                "failed",
                f"FHL Image Gen API URL/请求配置无效：{self._safe_text(exc)}",
            )

        last_failure = "FHL Images API 请求失败"
        for attempt in range(FHL_MAX_ATTEMPTS):
            request = Request(
                endpoint,
                data=body,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "Accept": "application/json",
                    "User-Agent": "YOLO11DamageDesktop/repair-render",
                },
                method="POST",
            )
            try:
                response = self.opener(request, timeout=240)
                raw = self._read_response(response, limit=_MAX_API_RESPONSE_BYTES)
                payload = json.loads(raw.decode("utf-8"))
                image = self._extract_image(payload)
                output = self._direct_output_path(source)
                temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
                temporary.write_bytes(image)
                os.replace(temporary, output)
                return RenderResult(str(source), str(output), "success", "FHL 修复渲染完成")
            except HTTPError as exc:
                last_failure = self._http_failure(exc)
                if exc.code not in FHL_RETRYABLE_STATUSES or attempt + 1 >= FHL_MAX_ATTEMPTS:
                    break
                self.sleep(self._retry_delay(exc))
            except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
                last_failure = f"FHL Images API 请求失败：{type(exc).__name__}: {self._safe_text(exc)}"
                break
        return RenderResult(str(source), None, "failed", last_failure)

    def render_one(self, source_path: str | Path, *, repair_method: str) -> RenderResult:
        source = Path(source_path)
        output = self._output_path(source)
        existing = next((item for item in self.manifest if item.get("source_path") == str(source)), None)
        existing_provider = str(existing.get("provider") or "fhl") if existing else ""
        existing_output = Path(str(existing.get("output_path"))) if existing and existing.get("output_path") else output
        if existing_output.is_file() and existing and existing.get("status") == "success" and existing_provider == "fhl":
            return RenderResult(str(source), str(existing_output), "resumed", "已沿用既有修復渲染圖")
        base_prompt = self.base_prompt or (
            "請以這張工程損傷照片為基礎，生成保守、可供工程溝通的修復後視覺化渲染圖。"
            "保留構件視角、材質與背景，僅修復可見損傷，不要改變構件幾何，"
            "不要新增構件，不要加入文字、水印或誇張效果。"
        )
        # The reviewed method is a required request fact even when the user
        # supplies a custom base prompt; otherwise the provider receives no
        # repair-specific instruction.
        prompt = f"{base_prompt.rstrip()}\n修复方案：{str(repair_method).strip()}。"
        if not source.is_file():
            return self._record(RenderResult(str(source), None, "failed", "找不到修复渲染源图片"))
        if self.api_key:
            return self._record(self._render_with_gui_api(source, prompt))
        if not self.script_path.is_file():
            return self._record(RenderResult(str(source), None, "failed", "找不到 FHL Image Gen 腳本"))
        before = {p for p in self.output_dir.glob("*.png")}
        command = [
            self.node_executable,
            str(self.script_path),
            "--edit",
            "--image",
            str(source),
            "--prompt",
            prompt,
            "--aspect",
            "4:3",
            "--output-dir",
            str(self.output_dir.resolve()),
        ]
        env = os.environ.copy()
        for name in ("FHL_API_KEY", "FHL_API_URL", "APIMART_API_KEY", "APIMART_BASE_URL"):
            env.pop(name, None)
        try:
            run_kwargs = {
                "capture_output": True,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
                "timeout": 240,
                "check": False,
                "env": env,
            }
            if os.name == "nt":
                run_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            result = self.runner(
                command,
                **run_kwargs,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return self._record(RenderResult(str(source), None, "failed", f"FHL 渲染執行失敗：{type(exc).__name__}"))
        if result.returncode != 0:
            return self._record(RenderResult(str(source), None, "failed", self._failure_message(result)))
        candidates = [p for p in self.output_dir.glob("*.png") if p not in before]
        if not candidates:
            return self._record(RenderResult(str(source), None, "failed", "FHL 已回傳但找不到輸出圖片"))
        generated = candidates[0]
        if generated.resolve() != output.resolve():
            output.write_bytes(generated.read_bytes())
            try:
                generated.unlink()
            except OSError:
                pass
        return self._record(RenderResult(str(source), str(output), "success", "修復渲染完成"))

    def render_many(
        self,
        items: list[tuple[str | Path, str]],
        *,
        should_stop: Callable[[], bool] | None = None,
        on_result: Callable[[RenderResult], None] | None = None,
    ) -> list[RenderResult]:
        results: list[RenderResult] = []
        for path, method in items:
            if should_stop is not None and should_stop():
                cancelled = self._record(
                    RenderResult(str(path), None, "cancelled", "已取消剩餘渲染任務")
                )
                results.append(cancelled)
                if on_result is not None:
                    on_result(cancelled)
                continue
            result = self.render_one(path, repair_method=method)
            results.append(result)
            if on_result is not None:
                on_result(result)
        return results

    def _record(self, result: RenderResult) -> RenderResult:
        self.manifest = [item for item in self.manifest if item.get("source_path") != result.source_path]
        self.manifest.append(
            {
                **asdict(result),
                "provider": "fhl",
                "model": FHL_IMAGE_MODEL,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        self._save_manifest()
        return result
