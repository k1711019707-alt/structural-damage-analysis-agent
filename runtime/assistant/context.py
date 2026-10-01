"""Read-only context resolution for current and historical detection artifacts."""
from __future__ import annotations

import hashlib
import json
import re
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from runtime.app_paths import default_output_root, user_data_root


@dataclass(frozen=True)
class DetectionRun:
    run_id: str
    source_folder: str
    output_dir: str
    started_at: str
    finished_at: str
    report_path: str = ""
    plan_path: str = ""
    summary_path: str = ""
    evidence_hash: str = ""
    status: str = ""
    results: tuple[dict[str, Any], ...] = ()
    summary_payload: dict[str, Any] = field(default_factory=dict)
    report_text: str = ""
    plan_text: str = ""

    @property
    def source_norm(self) -> str:
        return _normalize_path(self.source_folder)

    @property
    def output_norm(self) -> str:
        return _normalize_path(self.output_dir)


@dataclass
class ResolvedContext:
    prompt_context: str
    source_manifest: dict[str, Any] = field(default_factory=dict)
    snapshot: dict[str, Any] = field(default_factory=dict)
    ambiguous: tuple[DetectionRun, ...] = ()
    warnings: tuple[str, ...] = ()


def _normalize_path(value: str | Path) -> str:
    text = str(value or "").replace("/", "\\").strip().rstrip("\\")
    return text.casefold()


def _file_hash(path: Path) -> str:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return ""


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        result = datetime.fromisoformat(text)
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


class DetectionCatalog:
    """Catalogs only known `batch_summary.json` files; no arbitrary traversal is exposed."""

    def __init__(self, roots: Iterable[str | Path] = (), *, archive_root: str | Path | None = None) -> None:
        self.roots = tuple(dict.fromkeys(str(Path(root).resolve()) for root in roots if str(root)))
        self.archive_root = Path(archive_root) if archive_root else None

    def scan(self) -> list[DetectionRun]:
        paths: set[Path] = set()
        for root_text in self.roots:
            root = Path(root_text)
            if root.is_file() and root.name == "batch_summary.json":
                paths.add(root)
            elif root.is_dir():
                try:
                    paths.update(root.rglob("batch_summary.json"))
                except OSError:
                    continue
        runs_by_id: dict[str, DetectionRun] = {}
        for summary_path in sorted(paths, key=lambda item: str(item).casefold()):
            try:
                payload = json.loads(summary_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            output_dir = Path(str(payload.get("output_dir") or summary_path.parent)).resolve()
            source_folder = str(payload.get("source_folder") or output_dir.parent)
            evidence = payload.get("results") if isinstance(payload.get("results"), list) else []
            started = str(payload.get("started_at") or "")
            finished = str(payload.get("finished_at") or "")
            identity = f"{_normalize_path(output_dir)}|{started}|{_file_hash(summary_path)}"
            run_id = str(payload.get("run_id") or hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20])
            report_path = output_dir / "report.md"
            plan_path = output_dir / "construction_plan.md"
            runs_by_id[run_id] = DetectionRun(run_id, source_folder, str(output_dir), started, finished, str(report_path) if report_path.is_file() else "", str(plan_path) if plan_path.is_file() else "", str(summary_path), _file_hash(summary_path), str(payload.get("status") or ""), tuple(item for item in evidence if isinstance(item, dict)), dict(payload), _read_text(report_path), _read_text(plan_path))
        if self.archive_root is not None and self.archive_root.is_dir():
            for record_path in sorted(self.archive_root.glob("*.json")):
                try:
                    record = json.loads(record_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError):
                    continue
                summary = record.get("summary") if isinstance(record, dict) else None
                if not isinstance(summary, dict):
                    continue
                run_id = str(record.get("run_id") or summary.get("run_id") or "")
                if not run_id:
                    continue
                output_dir = str(summary.get("output_dir") or "")
                source_folder = str(summary.get("source_folder") or (Path(output_dir).parent if output_dir else ""))
                evidence = summary.get("results") if isinstance(summary.get("results"), list) else []
                report = record.get("report") if isinstance(record.get("report"), dict) else {}
                plan = record.get("plan") if isinstance(record.get("plan"), dict) else {}
                runs_by_id[run_id] = DetectionRun(run_id, source_folder, output_dir, str(summary.get("started_at") or ""), str(summary.get("finished_at") or ""), str(report.get("path") or ""), str(plan.get("path") or ""), str(record.get("summary_path") or ""), str(record.get("evidence_hash") or ""), str(summary.get("status") or ""), tuple(item for item in evidence if isinstance(item, dict)), dict(summary), str(report.get("content") or ""), str(plan.get("content") or ""))
        return sorted(runs_by_id.values(), key=lambda item: (_parse_time(item.started_at) or datetime.min.replace(tzinfo=timezone.utc), item.output_norm), reverse=True)

    def record(self, summary_path: str | Path, *, include_artifacts: bool = True) -> Path:
        """Persist an immutable assistant-owned snapshot for one registered run."""
        path = Path(summary_path).resolve()
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("batch_summary.json must contain an object")
        run_id = str(payload.get("run_id") or uuid.uuid4())
        payload["run_id"] = run_id
        output_dir = Path(str(payload.get("output_dir") or path.parent)).resolve()
        if self.archive_root is None:
            raise ValueError("archive_root is required when recording a detection snapshot")
        self.archive_root.mkdir(parents=True, exist_ok=True)
        record_path = self.archive_root / f"{run_id}.json"
        existing: dict[str, Any] = {}
        if record_path.is_file():
            try:
                candidate = json.loads(record_path.read_text(encoding="utf-8"))
                existing = candidate if isinstance(candidate, dict) else {}
            except (OSError, UnicodeError, json.JSONDecodeError):
                existing = {}
        record: dict[str, Any] = {
            "schema_version": "assistant-detection-snapshot.v1",
            "run_id": run_id,
            "summary_path": str(path),
            "evidence_hash": hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
            "summary": payload,
            "report": existing.get("report") if isinstance(existing.get("report"), dict) else {},
            "plan": existing.get("plan") if isinstance(existing.get("plan"), dict) else {},
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if include_artifacts:
            for key, filename in (("report", "report.md"), ("plan", "construction_plan.md")):
                artifact = output_dir / filename
                if artifact.is_file():
                    record[key] = {"path": str(artifact), "sha256": _file_hash(artifact), "content": _read_text(artifact)[:50000]}
        temporary = record_path.with_name(f".{record_path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, record_path)
        return record_path


class AssistantContextResolver:
    def __init__(self, *, current_output_dir: str | Path | None = None, source_folder: str | Path | None = None, project_root: str | Path | None = None, archive_root: str | Path | None = None) -> None:
        current = Path(current_output_dir).resolve() if current_output_dir else None
        source = Path(source_folder).resolve() if source_folder else (current.parent if current else None)
        roots = [item for item in (current, source, default_output_root(), user_data_root() / "Outputs", project_root) if item]
        self.current_output_dir = current
        resolved_archive_root = archive_root or (user_data_root() / "assistant" / "detection_runs")
        self.catalog = DetectionCatalog(roots, archive_root=resolved_archive_root)

    @staticmethod
    def _date_from_question(question: str) -> datetime | None:
        today = datetime.now().astimezone()
        if "昨天" in question:
            yesterday = today - timedelta(days=1)
            return yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
        if "今天" in question:
            return today.replace(hour=0, minute=0, second=0, microsecond=0)
        match = re.search(r"(20\d{2})[年\-/](\d{1,2})[月\-/](\d{1,2})", question)
        if not match:
            short = re.search(r"(?<!\d)(\d{1,2})月(\d{1,2})日", question)
            if short:
                try:
                    return datetime(today.year, int(short.group(1)), int(short.group(2)), tzinfo=today.tzinfo)
                except ValueError:
                    return None
        if not match:
            return None
        try:
            local_offset = datetime.now().astimezone().utcoffset() or timedelta(0)
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)), tzinfo=timezone(local_offset))
        except ValueError:
            return None

    @staticmethod
    def _ordinal(question: str) -> int | None:
        match = re.search(r"第\s*(\d+)\s*次|(?:第)?(一|二|三|四|五|六|七|八|九|十)次", question)
        if not match:
            return None
        if match.group(1):
            return int(match.group(1))
        return {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}.get(match.group(2))

    @staticmethod
    def _has_explicit_detection_context(question: str) -> bool:
        """Return whether the user explicitly asks about local detection artifacts.

        A generic engineering question must not become ambiguous merely because
        the catalog contains several historical runs.  Detection artifacts are
        still available when the user names them directly (or supplies a
        date/path/image/ordinal selector).
        """
        text = str(question or "")
        if AssistantContextResolver._date_from_question(text) is not None:
            return True
        if AssistantContextResolver._ordinal(text) is not None:
            return True
        if re.search(r"[A-Za-z]:[\\/]|路径|文件夹|目录|(?:jpg|jpeg|png|bmp|tif|tiff)\b", text, flags=re.I):
            return True
        return bool(
            re.search(
                r"检测(?:结果|批次|记录|图片|报告)?|识别结果|损伤结果|检测图|检测报告|施工方案|当前报告|当前方案|"
                r"历史检测|本次检测|上一次|最近一次|最新一次|当天全部|全部检测|所有检测|所有结果|"
                r"\breport\b|\bplan\b|\bbatch\b|\brun\b",
                text,
                flags=re.I,
            )
        )

    def _select_runs(self, question: str, runs: list[DetectionRun]) -> list[DetectionRun]:
        selected = runs
        explicit_context = self._has_explicit_detection_context(question)
        date = self._date_from_question(question)
        if date:
            end = date + timedelta(days=1)
            selected = [run for run in selected if (stamp := _parse_time(run.started_at)) and date <= stamp.astimezone(date.tzinfo) < end]
        path_terms = [str(item) for item in re.findall(r"[A-Za-z]:[\\/][^\s，。；;]+", question)]
        if not path_terms:
            path_terms.extend(str(item) for item in re.findall(r"([A-Za-z0-9_.-]{3,})\s*(?:路径|文件夹|目录)", question, flags=re.I))
        if path_terms:
            normalized_terms = [_normalize_path(item) for item in path_terms]
            selected = [run for run in selected if any(term in run.source_norm or term in run.output_norm or any(term in part for part in (run.source_norm + "\\" + run.output_norm).split("\\")) for term in normalized_terms)]
        image_names = [str(item) for item in re.findall(r"[A-Za-z0-9_.-]+\.(?:jpg|jpeg|png|bmp|tif|tiff)", question, flags=re.I)]
        if image_names:
            image_terms = {item.casefold() for item in image_names}
            selected = [run for run in selected if any(str(item.get("image_name") or Path(str(item.get("image_path") or "")).name).casefold() in image_terms for item in run.results)]
        ordinal = self._ordinal(question)
        if ordinal is not None:
            source_groups: dict[str, list[DetectionRun]] = {}
            for run in runs:
                source_groups.setdefault(run.source_norm, []).append(run)
            candidates = selected or runs
            selected = []
            for source in {run.source_norm for run in candidates}:
                group = sorted(source_groups.get(source, []), key=lambda item: _parse_time(item.started_at) or datetime.min.replace(tzinfo=timezone.utc))
                if 0 < ordinal <= len(group):
                    selected.append(group[ordinal - 1])
        if any(term in question for term in ("最近一次", "最新一次")) and selected:
            selected = [max(selected, key=lambda item: _parse_time(item.started_at) or datetime.min.replace(tzinfo=timezone.utc))]
        elif "上一次" in question and selected:
            candidates = selected
            if self.current_output_dir:
                current_norm = _normalize_path(self.current_output_dir)
                current_runs = [run for run in selected if run.output_norm == current_norm]
                if current_runs:
                    source_norm = max(current_runs, key=lambda item: _parse_time(item.started_at) or datetime.min.replace(tzinfo=timezone.utc)).source_norm
                    candidates = [run for run in selected if run.source_norm == source_norm]
            ordered = sorted(candidates, key=lambda item: _parse_time(item.started_at) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
            selected = ordered[1:2] if len(ordered) > 1 else []
        if not date and not path_terms and not image_names and ordinal is None and self.current_output_dir:
            current_norm = _normalize_path(self.current_output_dir)
            current = [run for run in runs if run.output_norm == current_norm]
            if current:
                selected = current[:1]
            elif not explicit_context:
                # No current output and no explicit artifact reference: this
                # is a general knowledge question, not a batch-selection
                # request.  Do not turn the whole catalog into an ambiguity.
                selected = []
        elif not date and not path_terms and not image_names and ordinal is None and not explicit_context:
            selected = []
        elif not date and not path_terms and not image_names and ordinal is None and explicit_context:
            # With an explicit but underspecified detection request, use the
            # newest known run as optional context.  The service still answers
            # if this context is absent or later proves ambiguous.
            selected = runs[:1]
        return selected

    def resolve(self, question: str) -> ResolvedContext:
        runs = self.catalog.scan()
        selected = self._select_runs(question, runs)
        # Detection context is optional evidence.  When an explicit selector
        # matches multiple runs, include the bounded matches rather than asking
        # the user to choose a batch or emitting an ordinary warning.
        chosen = selected[:8] if selected else []
        if not chosen:
            return ResolvedContext("", {})
        sections: list[str] = []
        source_manifest: dict[str, Any] = {"detection_runs": [self._run_manifest(run) for run in chosen]}
        for run in chosen[:8]:
            sections.extend([f"检测批次：{run.run_id}", f"输入路径：{run.source_folder}", f"输出路径：{run.output_dir}", f"开始时间：{run.started_at or '未记录'}", f"结束时间：{run.finished_at or '未记录'}", f"状态：{run.status or '未知'}"])
            if run.summary_payload:
                sections.append("结构化检测结果：\n" + json.dumps(run.summary_payload, ensure_ascii=False, indent=2)[:12000])
            for label, path, archived_text in (("损伤分析报告", run.report_path, run.report_text), ("施工方案", run.plan_path, run.plan_text)):
                if not path and not archived_text:
                    continue
                target = Path(path)
                text = archived_text or _read_text(target)
                if not text:
                    continue
                sections.append(f"{label}（{target.name}）：\n{text[:10000]}")
                source_manifest[f"{label}:{run.run_id}"] = {"path": str(target), "sha256": _file_hash(target), "run_id": run.run_id}
        snapshot = {"selected_runs": [self._run_manifest(run) for run in chosen], "source_manifest": source_manifest}
        return ResolvedContext("\n\n".join(sections), source_manifest, snapshot)

    @staticmethod
    def _run_manifest(run: DetectionRun) -> dict[str, Any]:
        return {"run_id": run.run_id, "source_folder": run.source_folder, "output_dir": run.output_dir, "started_at": run.started_at, "finished_at": run.finished_at, "summary_path": run.summary_path, "report_path": run.report_path, "plan_path": run.plan_path, "status": run.status}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8") if path.is_file() else ""
    except (OSError, UnicodeError):
        return ""


def record_detection_snapshot(summary_path: str | Path, *, include_artifacts: bool = True, archive_root: str | Path | None = None) -> Path:
    target_archive = archive_root or (user_data_root() / "assistant" / "detection_runs")
    return DetectionCatalog((), archive_root=target_archive).record(summary_path, include_artifacts=include_artifacts)
