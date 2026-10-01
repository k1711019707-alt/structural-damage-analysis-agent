from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from urllib.error import HTTPError


class _Response:
    def __init__(self, body: bytes) -> None:
        self._body = body
        self.headers = {"Content-Type": "application/json"}

    def read(self, _size: int = -1) -> bytes:
        return self._body

    def close(self) -> None:
        pass


def test_explicit_gui_fhl_config_drives_actual_edit_request(tmp_path: Path) -> None:
    from runtime.fhl_repair_renderer import FhlRepairRenderer

    source = tmp_path / "damage.jpg"
    source.write_bytes(b"source-image")
    captured: list[object] = []

    def opener(request, *, timeout):
        captured.extend((request, timeout))
        assert request.full_url == "https://images.example.test/v1/images/edits"
        assert request.get_header("Authorization") == "Bearer fhl-gui-secret"
        assert request.get_header("Content-type").startswith("multipart/form-data; boundary=")
        body = bytes(request.data)
        for expected in (
            b'name="image"',
            b'filename="damage.jpg"',
            b'name="model"',
            b"gpt-image-2",
            b'name="n"',
            b'name="size"',
            b"2048x1536",
            b'name="response_format"',
            b"b64_json",
            b"source-image",
            "保持构件几何和拍摄视角不变。".encode("utf-8"),
            "修复方案：裂缝封闭修复。".encode("utf-8"),
        ):
            assert expected in body
        return _Response(
            json.dumps(
                {"data": [{"b64_json": base64.b64encode(b"rendered-image").decode("ascii")}]}
            ).encode()
        )

    def plugin_runner(*_args, **_kwargs):
        raise AssertionError("explicit GUI FHL credentials must bypass plugin-global configuration")

    output_dir = tmp_path / "renders"
    result = FhlRepairRenderer(
        output_dir=output_dir,
        api_key="fhl-gui-secret",
        api_url="https://images.example.test/v1/images/edits",
        base_prompt="保持构件几何和拍摄视角不变。",
        opener=opener,
        runner=plugin_runner,
    ).render_one(source, repair_method="裂缝封闭修复")

    assert result.status == "success"
    assert result.output_path is not None
    assert Path(result.output_path).read_bytes() == b"rendered-image"
    assert len(captured) == 2
    manifest_text = (output_dir / "render_manifest.json").read_text(encoding="utf-8")
    assert "fhl-gui-secret" not in manifest_text


def test_gui_fhl_url_normalization_and_invalid_url_fail_closed(tmp_path: Path) -> None:
    from runtime.fhl_repair_renderer import FhlRepairRenderer

    source = tmp_path / "damage.png"
    source.write_bytes(b"source")
    called: list[str] = []

    def opener(request, *, timeout):
        called.append(request.full_url)
        return _Response(
            json.dumps({"data": [{"b64_json": base64.b64encode(b"png").decode("ascii")}]}).encode()
        )

    result = FhlRepairRenderer(
        output_dir=tmp_path / "root-url",
        api_key="fhl-key",
        api_url="https://images.example.test",
        opener=opener,
    ).render_one(source, repair_method="修复")
    assert result.status == "success"
    assert called == ["https://images.example.test/v1/images/edits"]

    def no_network(*_args, **_kwargs):
        raise AssertionError("invalid FHL URL must fail before network access")

    failed = FhlRepairRenderer(
        output_dir=tmp_path / "bad-url",
        api_key="fhl-key",
        api_url="https://images.example.test/v1/responses",
        opener=no_network,
    ).render_one(source, repair_method="修复")
    assert failed.status == "failed"
    assert "FHL Image Gen API URL" in failed.message


def test_gui_fhl_route_retries_gateway_errors_and_redacts_key(tmp_path: Path) -> None:
    from runtime.fhl_repair_renderer import FhlRepairRenderer

    source = tmp_path / "damage.jpg"
    source.write_bytes(b"source")
    secret = "fhl-secret-value"
    attempts: list[int] = []
    sleeps: list[float] = []

    def opener(request, *, timeout):
        attempts.append(timeout)
        raise HTTPError(
            request.full_url,
            502,
            "Bad Gateway",
            {"Retry-After": "60"},
            io.BytesIO(json.dumps({"error": {"message": f"upstream failed for {secret}"}}).encode()),
        )

    result = FhlRepairRenderer(
        output_dir=tmp_path / "renders",
        api_key=secret,
        api_url="https://images.example.test/v1/images/edits",
        opener=opener,
        sleep=sleeps.append,
    ).render_one(source, repair_method="修复")

    assert result.status == "failed"
    assert len(attempts) == 4
    assert len(sleeps) == 3
    assert "HTTP 502" in result.message
    assert "<redacted>" in result.message
    assert secret not in result.message
    assert secret not in (tmp_path / "renders" / "render_manifest.json").read_text(encoding="utf-8")


def test_gui_fhl_route_rejects_success_without_base64_image(tmp_path: Path) -> None:
    from runtime.fhl_repair_renderer import FhlRepairRenderer

    source = tmp_path / "damage.jpg"
    source.write_bytes(b"source")

    def opener(_request, *, timeout):
        assert timeout > 0
        return _Response(json.dumps({"data": [{"url": "https://cdn.example.test/image.png"}]}).encode())

    result = FhlRepairRenderer(
        output_dir=tmp_path / "renders",
        api_key="fhl-key",
        api_url="https://images.example.test/v1/images/edits",
        opener=opener,
    ).render_one(source, repair_method="修复")

    assert result.status == "failed"
    assert "base64" in result.message
    assert result.output_path is None


def test_empty_gui_fhl_key_preserves_legacy_plugin_fallback(tmp_path: Path) -> None:
    from runtime.fhl_repair_renderer import FhlRepairRenderer

    source = tmp_path / "damage.jpg"
    source.write_bytes(b"source")
    script = tmp_path / "generate.mjs"
    script.write_text("// fixture", encoding="utf-8")
    calls: list[list[str]] = []

    def runner(command, **_kwargs):
        calls.append(command)
        return type("Result", (), {"returncode": 1, "stdout": "", "stderr": "legacy failure"})()

    def no_direct_http(*_args, **_kwargs):
        raise AssertionError("empty GUI FHL key must preserve legacy plugin routing")

    result = FhlRepairRenderer(
        output_dir=tmp_path / "renders",
        script_path=script,
        api_key="",
        api_url="https://www.fhl.mom/v1/images/edits",
        opener=no_direct_http,
        runner=runner,
    ).render_one(source, repair_method="修复")

    assert result.status == "failed"
    assert len(calls) == 1
    assert "--edit" in calls[0]
