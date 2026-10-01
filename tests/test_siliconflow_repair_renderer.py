from __future__ import annotations

import io
import json
from pathlib import Path
from urllib.error import HTTPError


class _Response:
    def __init__(self, body: bytes, *, content_type: str = "application/json") -> None:
        self._body = body
        self.headers = {"Content-Type": content_type}

    def read(self, _size: int = -1) -> bytes:
        return self._body

    def close(self) -> None:
        pass


def test_siliconflow_renderer_submits_documented_edit_payload_and_downloads_result(
    tmp_path: Path,
) -> None:
    from runtime.siliconflow_repair_renderer import SiliconFlowRepairRenderer

    source = tmp_path / "损伤照片.jpg"
    source.write_bytes(b"source-image")
    requests = []

    def opener(request, *, timeout):
        requests.append((request, timeout))
        if request.full_url.endswith("/images/generations"):
            payload = json.loads(request.data.decode("utf-8"))
            assert payload["model"] == "Qwen/Qwen-Image-Edit-2509"
            assert payload["prompt"].endswith("修复方案：裂缝封闭修复。")
            assert payload["image"].startswith("data:image/jpeg;base64,")
            assert "image_size" not in payload
            assert request.get_header("Authorization") == "Bearer silicon-secret"
            assert request.get_header("X-enable-watermark") is None
            return _Response(
                json.dumps({"images": [{"url": "https://cdn.example.test/render.png"}]}).encode()
            )
        assert request.full_url == "https://cdn.example.test/render.png"
        return _Response(b"rendered-image", content_type="image/png")

    output_dir = tmp_path / "修復渲染"
    result = SiliconFlowRepairRenderer(
        output_dir=output_dir,
        api_key="silicon-secret",
        base_url="https://api.siliconflow.cn/v1",
        model="Qwen/Qwen-Image-Edit-2509",
        base_prompt="保持构件几何和拍摄视角不变。",
        opener=opener,
    ).render_one(source, repair_method="裂缝封闭修复")

    assert result.status == "success"
    assert result.output_path is not None
    assert Path(result.output_path).read_bytes() == b"rendered-image"
    assert len(requests) == 2
    manifest = json.loads((output_dir / "render_manifest.json").read_text(encoding="utf-8"))
    assert manifest["renders"][0]["provider"] == "siliconflow"
    assert manifest["renders"][0]["model"] == "Qwen/Qwen-Image-Edit-2509"
    assert manifest["renders"][0]["output_path"] == result.output_path
    assert "cdn.example.test" not in json.dumps(manifest)


def test_siliconflow_renderer_rejects_missing_key_without_network(tmp_path: Path) -> None:
    from runtime.siliconflow_repair_renderer import SiliconFlowRepairRenderer

    source = tmp_path / "damage.jpg"
    source.write_bytes(b"source")

    def opener(*_args, **_kwargs):
        raise AssertionError("missing credentials must fail before network access")

    result = SiliconFlowRepairRenderer(
        output_dir=tmp_path / "renders",
        api_key="",
        opener=opener,
    ).render_one(source, repair_method="修复")

    assert result.status == "failed"
    assert "API Key" in result.message
    assert result.output_path is None


def test_siliconflow_renderer_records_missing_image_url(tmp_path: Path) -> None:
    from runtime.siliconflow_repair_renderer import SiliconFlowRepairRenderer

    source = tmp_path / "damage.jpg"
    source.write_bytes(b"source")

    def opener(_request, *, timeout):
        assert timeout > 0
        return _Response(json.dumps({"images": []}).encode())

    result = SiliconFlowRepairRenderer(
        output_dir=tmp_path / "renders",
        api_key="test-key",
        opener=opener,
    ).render_one(source, repair_method="修复")

    assert result.status == "failed"
    assert "图片 URL" in result.message
    assert result.output_path is None


def test_siliconflow_renderer_preserves_http_diagnostic_and_redacts_key(tmp_path: Path) -> None:
    from runtime.siliconflow_repair_renderer import SiliconFlowRepairRenderer

    source = tmp_path / "damage.jpg"
    source.write_bytes(b"source")
    secret = "silicon-secret-value"

    def opener(request, *, timeout):
        assert timeout > 0
        raise HTTPError(
            request.full_url,
            503,
            "Service Unavailable",
            {},
            io.BytesIO(
                json.dumps({"code": 50505, "message": f"overloaded for {secret}"}).encode()
            ),
        )

    result = SiliconFlowRepairRenderer(
        output_dir=tmp_path / "renders",
        api_key=secret,
        opener=opener,
    ).render_one(source, repair_method="修复")

    assert result.status == "failed"
    assert "HTTP 503" in result.message
    assert "50505" in result.message
    assert "<redacted>" in result.message
    assert secret not in result.message
    manifest_text = (tmp_path / "renders" / "render_manifest.json").read_text(encoding="utf-8")
    assert secret not in manifest_text

