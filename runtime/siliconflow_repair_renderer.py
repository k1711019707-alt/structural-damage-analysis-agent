from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from runtime.fhl_repair_renderer import FHL_PROMPT_VERSION, RenderResult


DEFAULT_SILICONFLOW_BASE_URL = "https://api.siliconflow.cn/v1"
DEFAULT_SILICONFLOW_MODEL = "Qwen/Qwen-Image-Edit-2509"
_MAX_API_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_IMAGE_BYTES = 50 * 1024 * 1024


class SiliconFlowRepairRenderer:
    """Render repair previews through SiliconFlow's JSON image-edit contract."""

    def __init__(
        self,
        *,
        output_dir: str | Path,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        base_prompt: str | None = None,
        opener: Callable[..., object] | None = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.api_key = str(api_key or "").strip()
        self.base_url = str(base_url or DEFAULT_SILICONFLOW_BASE_URL).strip()
        self.model = str(model or DEFAULT_SILICONFLOW_MODEL).strip()
        self.base_prompt = str(base_prompt or "").strip()
        self.opener = opener or urlopen
        self.manifest_path = self.output_dir / "render_manifest.json"
        self.manifest: list[dict[str, object]] = self._load_manifest()

    def _load_manifest(self) -> list[dict[str, object]]:
        if not self.manifest_path.is_file():
            return []
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            return list(payload.get("renders", []))
        except (OSError, TypeError, json.JSONDecodeError):
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

    def _record(self, result: RenderResult) -> RenderResult:
        self.manifest = [item for item in self.manifest if item.get("source_path") != result.source_path]
        self.manifest.append(
            {
                **asdict(result),
                "provider": "siliconflow",
                "model": self.model,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        self._save_manifest()
        return result

    def _safe_text(self, value: object, *, limit: int = 500) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if self.api_key:
            text = text.replace(self.api_key, "<redacted>")
        return text[:limit].rstrip()

    def _endpoint(self) -> str:
        base = self.base_url.rstrip("/")
        parsed = urlsplit(base)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("硅基流动 API URL 无效")
        if parsed.path.rstrip("/").endswith("/images/generations"):
            return base
        return f"{base}/images/generations"

    @staticmethod
    def _read_response(response: object, *, limit: int) -> bytes:
        try:
            body = response.read(limit + 1)  # type: ignore[attr-defined]
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        if len(body) > limit:
            raise ValueError("响应数据超过允许大小")
        return body

    def _http_failure(self, stage: str, exc: HTTPError) -> str:
        try:
            body = exc.read(_MAX_API_RESPONSE_BYTES + 1)
        except OSError:
            body = b""
        detail = ""
        if body:
            try:
                payload = json.loads(body.decode("utf-8", errors="replace"))
                if isinstance(payload, dict):
                    code = payload.get("code")
                    message = payload.get("message") or payload.get("error") or payload.get("data")
                    detail = " ".join(
                        part for part in (f"code={code}" if code is not None else "", str(message or "")) if part
                    )
                else:
                    detail = str(payload)
            except json.JSONDecodeError:
                detail = body.decode("utf-8", errors="replace")
        prefix = f"硅基流动{stage}失败（HTTP {exc.code}）"
        safe_detail = self._safe_text(detail)
        return f"{prefix}：{safe_detail}" if safe_detail else prefix

    def _request_json(self, payload: dict[str, object]) -> dict[str, object]:
        request = Request(
            self._endpoint(),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "YOLO11DamageDesktop/repair-render",
            },
            method="POST",
        )
        response = self.opener(request, timeout=240)
        body = self._read_response(response, limit=_MAX_API_RESPONSE_BYTES)
        parsed = json.loads(body.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("硅基流动返回格式不是 JSON 对象")
        return parsed

    def _download_image(self, remote_url: str) -> tuple[bytes, str]:
        parsed = urlsplit(remote_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("硅基流动返回了无效的图片 URL")
        request = Request(
            remote_url,
            headers={"Accept": "image/*", "User-Agent": "YOLO11DamageDesktop/repair-render"},
            method="GET",
        )
        response = self.opener(request, timeout=120)
        content_type = str(getattr(response, "headers", {}).get("Content-Type", "")).split(";", 1)[0].lower()
        body = self._read_response(response, limit=_MAX_IMAGE_BYTES)
        if not body:
            raise ValueError("硅基流动图片下载结果为空")
        extension = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
        }.get(content_type)
        if extension is None:
            candidate = Path(parsed.path).suffix.lower()
            extension = candidate if candidate in {".jpg", ".jpeg", ".png", ".webp"} else ".png"
        if extension == ".jpeg":
            extension = ".jpg"
        return body, extension

    def _resumed_result(self, source: Path) -> RenderResult | None:
        existing = next(
            (
                item
                for item in self.manifest
                if item.get("source_path") == str(source)
                and item.get("provider") == "siliconflow"
                and item.get("model") == self.model
                and item.get("status") == "success"
            ),
            None,
        )
        output_path = str(existing.get("output_path") or "") if existing else ""
        if output_path and Path(output_path).is_file():
            return RenderResult(str(source), output_path, "resumed", "已沿用既有硅基流动修复渲染图")
        return None

    def render_one(self, source_path: str | Path, *, repair_method: str) -> RenderResult:
        source = Path(source_path)
        resumed = self._resumed_result(source)
        if resumed is not None:
            return resumed
        if not source.is_file():
            return self._record(RenderResult(str(source), None, "failed", "找不到修复渲染源图片"))
        if not self.api_key:
            return self._record(
                RenderResult(str(source), None, "failed", "未配置硅基流动 API Key，请先在设置中填写")
            )
        if not self.model:
            return self._record(RenderResult(str(source), None, "failed", "未配置硅基流动图像模型"))

        mime_type = mimetypes.guess_type(source.name)[0] or "image/jpeg"
        if not mime_type.startswith("image/"):
            mime_type = "image/jpeg"
        image_data = base64.b64encode(source.read_bytes()).decode("ascii")
        instruction = self.base_prompt or (
            "保持构件几何形态、材质、背景和拍摄视角不变，仅依据已确认方案修复可见损伤；"
            "不要新增构件、文字、水印或夸张效果。"
        )
        prompt = f"{instruction.rstrip()} 修复方案：{str(repair_method).strip()}。"

        try:
            response = self._request_json(
                {
                    "model": self.model,
                    "prompt": prompt,
                    "image": f"data:{mime_type};base64,{image_data}",
                }
            )
        except HTTPError as exc:
            return self._record(RenderResult(str(source), None, "failed", self._http_failure("生图请求", exc)))
        except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            message = self._safe_text(exc)
            return self._record(
                RenderResult(str(source), None, "failed", f"硅基流动生图请求失败：{type(exc).__name__}: {message}")
            )

        images = response.get("images")
        remote_url = ""
        if isinstance(images, list) and images and isinstance(images[0], dict):
            remote_url = str(images[0].get("url") or "").strip()
        if not remote_url:
            return self._record(
                RenderResult(str(source), None, "failed", "硅基流动响应中没有可用的图片 URL")
            )

        try:
            image_bytes, extension = self._download_image(remote_url)
            output = self.output_dir / f"{source.stem}__修復渲染{extension}"
            temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
            temporary.write_bytes(image_bytes)
            os.replace(temporary, output)
        except HTTPError as exc:
            return self._record(RenderResult(str(source), None, "failed", self._http_failure("图片下载", exc)))
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            message = self._safe_text(exc)
            return self._record(
                RenderResult(str(source), None, "failed", f"硅基流动图片下载失败：{type(exc).__name__}: {message}")
            )
        return self._record(RenderResult(str(source), str(output), "success", "硅基流动修复渲染完成"))

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
                result = self._record(RenderResult(str(path), None, "cancelled", "已取消剩余渲染任务"))
            else:
                result = self.render_one(path, repair_method=method)
            results.append(result)
            if on_result is not None:
                on_result(result)
        return results
