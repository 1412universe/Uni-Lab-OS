from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_DEVICE_IDS = {
    "gn_workstation",
    "gn_robotic_arm",
    "gn_centrifuge_tube_liquid_handling",
    "gn_quick_carrier_exchange",
    "gn_solid_weighing",
    "gn_rotary_stack",
    "gn_stack",
    "gn_centrifuge",
    "gn_standard_oven",
    "gn_vacuum_oven",
    "gn_locking_mechanism",
    "gn_prcxi",
}
EXPECTED_WORKFLOWS = {
    "900f9f79-7dfb-42f2-89ce-f70e86d6b68c": "gn_lab/workflows/quick_change_debug.py",
    "5d2b64d1-2666-4459-a587-708aadf0a5b3": "gn_lab/workflows/gold_tetrahedron_after_first_centrifuge.py",
    "4b868761-9468-46ec-86c5-7ab64b6b8854": "gn_lab/workflows/gold_tetrahedron_aa_mix.py",
    "a138bc18-9c16-4552-8696-e4f43903b9ad": "gn_lab/workflows/gold_tetrahedron_ctac_ctab.py",
    "8a15d439-59ab-43a3-928a-1aa8350d260f": "gn_lab/workflows/prcxi_9320_transfer.py",
}


def test_required_files_exist() -> None:
    required = [
        ROOT / "package.yaml",
        ROOT / "gn_lab" / "devices" / "gn_station_devices.py",
        ROOT / "deployment" / "graphs" / "gn-station.json",
        ROOT / "deployment" / "graphs" / "dry-run.json",
        ROOT / "deployment" / "local_config.py",
        *(ROOT / source for source in EXPECTED_WORKFLOWS.values()),
    ]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
    assert missing == []


def test_package_yaml_registers_legacy_workflows() -> None:
    manifest = (ROOT / "package.yaml").read_text(encoding="utf-8")
    for workflow_uuid, source in EXPECTED_WORKFLOWS.items():
        assert workflow_uuid in manifest
        assert source in manifest
        text = (ROOT / source).read_text(encoding="utf-8")
        assert f'workflow_uuid="{workflow_uuid}"' in text
        assert "unilab:node_uuid=" in text


def test_graph_uses_community_gn_lab_classes() -> None:
    graph = json.loads((ROOT / "deployment" / "graphs" / "gn-station.json").read_text(encoding="utf-8"))
    device_nodes = [node for node in graph["nodes"] if node.get("type") == "device" and node.get("class")]
    classes = {node["class"] for node in device_nodes}
    assert all(item.startswith("community.gn_lab.") for item in classes)
    assert "community.gn_lab.gn_workstation" in classes
    assert "community.gn_lab.gn_prcxi" in classes
    prcxi = next(node for node in graph["nodes"] if node.get("id") == "PRCXI")
    assert "auto_connect" not in (prcxi.get("config") or {})


def test_dry_run_graph_disables_hardware_connect() -> None:
    graph = json.loads((ROOT / "deployment" / "graphs" / "dry-run.json").read_text(encoding="utf-8"))
    devices = [node for node in graph["nodes"] if node.get("type") == "device"]
    assert devices
    opc_devices = [node for node in devices if node.get("id") != "PRCXI"]
    assert all(node.get("config", {}).get("auto_connect") is False for node in opc_devices)
    prcxi = next(node for node in devices if node["id"] == "PRCXI")
    assert "auto_connect" not in (prcxi.get("config") or {})
    assert prcxi["config"]["simulator"] is True
    assert prcxi["config"]["setup"] is False
    device_ids = {node["id"] for node in devices}
    assert {
        "gn_workstation",
        "gn_robotic_arm",
        "gn_centrifuge_tube_liquid_handling",
        "gn_quick_carrier_exchange",
        "gn_solid_weighing",
        "gn_rotary_stack",
        "gn_centrifuge",
        "gn_standard_oven",
        "gn_vacuum_oven",
        "gn_locking_mechanism",
        "PRCXI",
    } <= device_ids
    locking = next(node for node in devices if node["id"] == "gn_locking_mechanism")
    assert locking["class"] == "community.gn_lab.gn_locking_mechanism"
    assert "opcua_gn1.3.9.csv" in {
        node.get("config", {}).get("csv_path") or node.get("config", {}).get("xlsx_path")
        for node in devices
        if node["id"] != "PRCXI"
    }


def test_ast_scan_finds_all_gn_devices() -> None:
    from concurrent.futures import ThreadPoolExecutor

    from unilabos.registry.ast_registry_scanner import scan_directory

    with ThreadPoolExecutor(max_workers=4) as executor:
        result = scan_directory(ROOT / "gn_lab", python_path=ROOT, executor=executor)
    device_ids = set(result["devices"].keys())
    assert EXPECTED_DEVICE_IDS <= device_ids
    resources = result.get("resources", {})
    assert "prcxi_deck" in resources
    assert "prcxi_nest_1_troughplate" in resources
    assert "prcxi_96_deepwell" in resources
    assert "prcxi_1250ul_tips" in resources
    prcxi = result["devices"]["gn_prcxi"]
    for action_name in ("create_protocol", "run_protocol"):
        assert action_name in prcxi.get("actions", {})
        assert not (prcxi["actions"][action_name].get("action_args") or {}).get("auto_prefix")
    centrifuge = result["devices"]["gn_centrifuge"]
    assert "execute_command" in centrifuge.get("actions", {}) or "auto-execute_command" in {
        f"auto-{name}" for name in centrifuge.get("actions", {})
    } or "execute_command" in centrifuge.get("auto_methods", {})
    for device_id in (
        "gn_workstation",
        "gn_robotic_arm",
        "gn_centrifuge_tube_liquid_handling",
        "gn_standard_oven",
        "gn_vacuum_oven",
        "gn_locking_mechanism",
    ):
        entry = result["devices"][device_id]
        assert entry.get("actions") or entry.get("auto_methods"), device_id
