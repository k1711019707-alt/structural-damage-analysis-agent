"""Back up a descriptor's collection or restore a portable active version."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from knowledge_pipeline.qdrant_backup import export_snapshot, restore_snapshot
from runtime.app_paths import resolve_knowledge_portable_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("descriptor", type=Path)
    export.add_argument("output", type=Path)
    restore = commands.add_parser("restore")
    restore.add_argument("--active-manifest", type=Path)
    restore.add_argument("--descriptor", type=Path)
    restore.add_argument("--snapshot", type=Path)
    restore.add_argument("--sha256")
    args = parser.parse_args()
    if args.command == "export":
        result = export_snapshot(args.descriptor, args.output)
        metadata = args.output.with_name(args.output.name + ".json")
        metadata.write_text(json.dumps(result, indent=2), encoding="utf-8")
    elif args.active_manifest:
        if args.descriptor or args.snapshot or args.sha256:
            parser.error("use --active-manifest or --descriptor/--snapshot/--sha256, not both")
        manifest = args.active_manifest.resolve()
        active = json.loads(manifest.read_text(encoding="utf-8"))
        backup = active["qdrant_snapshot"]
        descriptor = resolve_knowledge_portable_path(active["semantic_index_path"], root=manifest.parent)
        snapshot = resolve_knowledge_portable_path(backup["path"], root=manifest.parent)
        if descriptor is None or snapshot is None:
            raise ValueError("qdrant_snapshot_paths_invalid")
        result = restore_snapshot(descriptor, snapshot, expected_sha256=backup["sha256"])
    else:
        if not (args.descriptor and args.snapshot and args.sha256):
            parser.error("restore requires --active-manifest or --descriptor, --snapshot and --sha256")
        result = restore_snapshot(args.descriptor, args.snapshot, expected_sha256=args.sha256)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
