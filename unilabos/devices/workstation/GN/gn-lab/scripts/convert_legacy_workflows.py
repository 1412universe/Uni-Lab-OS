#!/usr/bin/env python3
"""把 GN 目录下的云端 ILab JSON 工作流转成 gn-lab Python 源码。"""

from __future__ import annotations

import json
import keyword
import re
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
GN_ROOT = ROOT.parent
OUT_DIR = ROOT / "gn_lab" / "workflows"

WORKFLOWS = [
    (
        "快换模块单点调试（从复位到所有功能再到复位）.json",
        "quick_change_debug.py",
        "quick_change_debug",
    ),
    (
        "金四面体-从第一次离心到结束.json",
        "gold_tetrahedron_after_first_centrifuge.py",
        "gold_tetrahedron_after_first_centrifuge",
    ),
    (
        "金四面体AA溶液制备与溶液的混合，并放到T16等待离心.json",
        "gold_tetrahedron_aa_mix.py",
        "gold_tetrahedron_aa_mix",
    ),
    (
        "金四面体CTAC与CTAB混合溶液制备.json",
        "gold_tetrahedron_ctac_ctab.py",
        "gold_tetrahedron_ctac_ctab",
    ),
    (
        "9320移液.json",
        "prcxi_9320_transfer.py",
        "prcxi_9320_transfer",
    ),
]

DEVICE_MAP = {
    "gn_quick_carrier_exchange": (
        "gn_lab.devices.gn_station_devices",
        "QuickCarrierExchangeDevice",
        "quick_change",
        "gn_quick_carrier_exchange",
    ),
    "gn_robotic_arm": (
        "gn_lab.devices.gn_station_devices",
        "RoboticArmDevice",
        "robot",
        "gn_robotic_arm",
    ),
    "gn_centrifuge": (
        "gn_lab.devices.gn_station_devices",
        "CentrifugeDevice",
        "centrifuge",
        "gn_centrifuge",
    ),
    "gn_solid_weighing": (
        "gn_lab.devices.gn_station_devices",
        "SolidWeighingDevice",
        "solid",
        "gn_solid_weighing",
    ),
    "gn_rotary_stack": (
        "gn_lab.devices.gn_station_devices",
        "RotaryStackDevice",
        "stack",
        "gn_rotary_stack",
    ),
    "gn_stack": (
        "gn_lab.devices.gn_station_devices",
        "StackDevice",
        "legacy_stack",
        "gn_stack",
    ),
    "PRCXI": (
        "gn_lab.devices.gn_prcxi",
        "GN_PRCXI9300Handler",
        "prcxi",
        "PRCXI",
    ),
}

KWARG_RENAME = {"from": "from_"}


def ident(name: str, fallback: str) -> str:
    cleaned = re.sub(r"[^0-9a-zA-Z_]", "_", name)
    if not cleaned or cleaned[0].isdigit() or keyword.iskeyword(cleaned):
        cleaned = f"{fallback}_{cleaned}" if cleaned else fallback
    return cleaned


def py_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if value is None:
        return "None"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        if not value:
            return "[]"
        return "[" + ", ".join(py_literal(item) for item in value) + "]"
    if isinstance(value, dict):
        items = ", ".join(
            f"{py_literal(key)}: {py_literal(item)}" for key, item in value.items()
        )
        return "{" + items + "}"
    return repr(value)


def method_name(node: dict[str, Any]) -> str:
    raw = str(node.get("template_name") or node.get("name") or "")
    return raw[5:] if raw.startswith("auto-") else raw


METHOD_PARAMS: dict[tuple[str, str], set[str]] = {
    ("gn_quick_carrier_exchange", "execute_command"): {
        "cmd_type", "x_pos", "top_z_pos", "take_z_pos", "push_pos", "push_z_pos",
        "x_speed", "z1_speed", "z2_speed", "push_speed", "z3_speed",
        "stir_rpm", "stir_temp", "stir_time_minutes", "timeout",
    },
    ("gn_robotic_arm", "pick"): {"station", "item_type", "number", "x_speed", "timeout"},
    ("gn_robotic_arm", "place"): {"station", "item_type", "number", "x_speed", "timeout"},
    ("gn_centrifuge", "execute_command"): {
        "cmd_type", "y_pos", "z_pos", "inner_z_pos", "rpm", "time_minutes",
        "y_speed", "z_speed", "plate_no", "temperature", "timeout",
    },
    ("gn_solid_weighing", "execute_command"): {
        "cmd_type", "x_pos", "y_pos", "material_z_pos", "gripper_z_pos", "door_pos",
        "volune_weight", "x_speed", "y_speed", "material_z_speed", "gripper_z_speed",
        "door_speed", "remove_static", "timeout",
    },
    ("gn_rotary_stack", "execute_command"): {
        "cmd_type", "r_pos", "z_pos", "r_speed", "z_speed", "timeout",
    },
    ("gn_stack", "execute_command"): {
        "cmd_type", "r_pos", "z_pos", "r_speed", "z_speed", "timeout",
    },
    ("PRCXI", "move_plate"): {
        "plate", "to", "from", "from_", "intermediate_locations", "pickup_offset",
        "destination_offset", "drop_direction", "pickup_direction",
        "pickup_distance_from_top", "hierarchy", "pinch_it_up_position",
        "put_down_position", "force", "get_direction", "put_direction",
        "resource_offset", "*",
    },
    ("PRCXI", "transfer_liquid"): {"*"},
    ("PRCXI", "titration_liquid"): {"*"},
    ("PRCXI", "create_protocol"): {
        "protocol_name", "protocol_description", "protocol_version",
        "protocol_author", "protocol_date", "protocol_type", "none_keys",
    },
    ("PRCXI", "run_protocol"): {"protocol_id"},
    ("PRCXI", "shaking_incubation_action"): {
        "time", "module_no", "amplitude", "is_wait", "temperature",
    },
}


def _resource_slot_literal(value: Any) -> Any:
    """物料占位符只保留 uuid，避免图校验因 id/name 额外字段失败。"""

    if isinstance(value, dict):
        uuid = value.get("uuid")
        if isinstance(uuid, str) and uuid and ("id" in value or "name" in value):
            return {"uuid": uuid}
        return {key: _resource_slot_literal(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_resource_slot_literal(item) for item in value]
    return value


def call_kwargs(node: dict[str, Any]) -> str:
    payload = dict(node.get("param") or {})
    action = method_name(node)
    allowed = METHOD_PARAMS.get((str(node.get("device_name")), action), {"*"})
    parts: list[str] = []
    for key, value in payload.items():
        if "*" not in allowed and key not in allowed and KWARG_RENAME.get(key) not in allowed:
            continue
        py_key = KWARG_RENAME.get(key, key)
        if not py_key.isidentifier() or keyword.iskeyword(py_key):
            continue
        parts.append(f"{py_key}={py_literal(_resource_slot_literal(value))}")
    return ", ".join(parts)


def topo_ilab_nodes(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ilab = [node for node in nodes if node.get("type") == "ILab"]
    ids = {str(node["uuid"]) for node in ilab}
    incoming: dict[str, int] = {node_id: 0 for node_id in ids}
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        source = str(edge.get("source_node_uuid") or "")
        target = str(edge.get("target_node_uuid") or "")
        if source in ids and target in ids and source != target:
            outgoing[source].append(target)
            incoming[target] += 1
    ready = deque(node_id for node_id, count in incoming.items() if count == 0)
    order: list[str] = []
    while ready:
        current = ready.popleft()
        order.append(current)
        for nxt in outgoing[current]:
            incoming[nxt] -= 1
            if incoming[nxt] == 0:
                ready.append(nxt)
    remaining = [node_id for node_id in ids if node_id not in order]
    order.extend(remaining)
    by_id = {str(node["uuid"]): node for node in ilab}
    return [by_id[node_id] for node_id in order]


def convert_one(source: Path, dest: Path, function_name: str) -> dict[str, str]:
    payload = json.loads(source.read_text(encoding="utf-8"))
    inner = payload["data"] if isinstance(payload.get("data"), dict) else payload
    workflow_uuid = str(inner["workflow_uuid"])
    displayname = str(inner.get("workflow_name") or payload.get("name") or function_name)
    nodes = inner.get("nodes") or []
    edges = inner.get("edges") or []
    ilab_nodes = topo_ilab_nodes(nodes, edges)

    used_devices: dict[str, tuple[str, str, str, str]] = {}
    for node in ilab_nodes:
        device_name = str(node.get("device_name") or "")
        if device_name not in DEVICE_MAP:
            raise SystemExit(f"{source.name}: 未映射设备 {device_name}")
        used_devices[device_name] = DEVICE_MAP[device_name]

    imports: list[str] = [
        "from unilabos.workflow.authoring import device, workflow",
        "",
    ]
    by_module: dict[str, list[str]] = defaultdict(list)
    for module, class_name, _, _ in used_devices.values():
        if class_name not in by_module[module]:
            by_module[module].append(class_name)
    for module, class_names in sorted(by_module.items()):
        imports.append(f"from {module} import {', '.join(class_names)}")
    imports.append("")

    bindings: list[str] = []
    handle_by_device: dict[str, str] = {}
    for device_name, (module, class_name, handle, instance_id) in used_devices.items():
        handle_by_device[device_name] = handle
        bindings.append(f"{handle}: {class_name} = device({json.dumps(instance_id)})")
    bindings.append("")

    body: list[str] = []
    for index, node in enumerate(ilab_nodes, start=1):
        node_uuid = str(node["uuid"])
        handle = handle_by_device[str(node["device_name"])]
        action = method_name(node)
        kwargs = call_kwargs(node)
        var = f"step_{index:03d}"
        footer = str(node.get("footer") or "").strip()
        if footer:
            body.append(f"    # {footer}")
        body.append(f"    # unilab:node_uuid={node_uuid}")
        call = f"{handle}.{action}({kwargs})" if kwargs else f"{handle}.{action}()"
        body.append(f"    {var} = {call}")

    lines = [
        f'"""从 legacy JSON 导入：{source.name}。"""',
        "",
        *imports,
        *bindings,
        "@workflow(",
        f'    workflow_uuid="{workflow_uuid}",',
        f"    displayname={json.dumps(displayname, ensure_ascii=False)},",
        f'    description="由 {source.name} 转换，节点身份保持不变。",',
        ")",
        f"def {function_name}() -> None:",
        *body,
        "",
    ]
    dest.write_text("\n".join(lines), encoding="utf-8")
    return {
        "workflow_uuid": workflow_uuid,
        "source": str(dest.relative_to(ROOT)).replace("\\", "/"),
        "function": function_name,
        "nodes": str(len(ilab_nodes)),
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "__init__.py").write_text("", encoding="utf-8")
    rows = []
    for json_name, py_name, function_name in WORKFLOWS:
        info = convert_one(GN_ROOT / json_name, OUT_DIR / py_name, function_name)
        rows.append(info)
        print(f"wrote {info['source']} uuid={info['workflow_uuid']} nodes={info['nodes']}")
    print("package.yaml entries:")
    for row in rows:
        print(f"  - workflow_uuid: {row['workflow_uuid']}")
        print(f"    source: {row['source']}")


if __name__ == "__main__":
    main()
