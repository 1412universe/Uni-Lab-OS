from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_UUID = "7e2a9c14-6b5d-4f81-a3c0-91d8e4b2f6a5"


def test_required_files_exist() -> None:
    required = [
        ROOT / "package.yaml",
        ROOT / "demo_lab" / "devices" / "transport_robot.py",
        ROOT / "demo_lab" / "devices" / "stations.py",
        ROOT / "demo_lab" / "resources" / "materials.py",
        ROOT / "demo_lab" / "workflows" / "weigh_aliquot.py",
        ROOT / "deployment" / "graphs" / "dry-run.json",
        ROOT / "README.md",
    ]
    assert [str(path.relative_to(ROOT)) for path in required if not path.is_file()] == []


def test_package_yaml_matches_source_uuid() -> None:
    manifest = (ROOT / "package.yaml").read_text(encoding="utf-8")
    source = "demo_lab/workflows/weigh_aliquot.py"
    assert WORKFLOW_UUID in manifest
    assert source in manifest
    assert f'workflow_uuid="{WORKFLOW_UUID}"' in (ROOT / source).read_text(encoding="utf-8")


def test_workflow_node_uuids_are_unique() -> None:
    pattern = re.compile(r"unilab:node_uuid=([0-9a-fA-F-]{36})")
    text = (ROOT / "demo_lab" / "workflows" / "weigh_aliquot.py").read_text(encoding="utf-8")
    seen = pattern.findall(text)
    for item in seen:
        uuid.UUID(item)
    assert len(seen) == len(set(seen)) >= 10


def test_graphs_cover_case_objects() -> None:
    payload = json.loads((ROOT / "deployment" / "graphs" / "dry-run.json").read_text(encoding="utf-8"))
    ids = {node["id"] for node in payload["nodes"]}
    assert {"transport_robot_01", "balance_station_01", "pipette_station_01", "cap_station_01", "stock_vial_001", "aliquot_vial_001", "tipbox_001"} <= ids
