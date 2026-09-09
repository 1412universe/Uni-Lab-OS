#!/usr/bin/env python3
"""Patch 金四面体 workflow JSON per updated pre-incubation + centrifuge logic."""

from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path
from typing import Any

SRC = Path(r"c:\Users\Administrator\Downloads\金四面体（从堆栈到三次离心） (2).json")
OUT_DOWNLOADS = SRC
OUT_REPO = Path(__file__).resolve().parent / "金四面体（从堆栈到三次离心）_updated.json"

# --- 资源引用 ---
RES = {
    "reaction_plate": {
        "id": "/gn_workstation/PRCXI/PRCXI_Deck/PRCXI_96_DeepWell5",
        "name": "PRCXI_96_DeepWell5",
        "uuid": "e74c153c-a29f-4653-a665-b9e1cc1711a9",
    },
    "mix_plate": {
        "id": "/gn_workstation/PRCXI/PRCXI_Deck/PRCXI_96_DeepWell_3",
        "name": "PRCXI_96_DeepWell_3",
        "uuid": "f260b8ba-a629-42ea-8340-548d765b4479",
    },
    "stack4_plate": {
        "id": "/gn_workstation/PRCXI/PRCXI_Deck/PRCXI_96_DeepWell_3",
        "name": "PRCXI_96_DeepWell_3",
        "uuid": "f260b8ba-a629-42ea-8340-548d765b4479",
    },
    "water": {
        "id": "/gn_workstation/PRCXI/PRCXI_nest_1_troughplate4",
        "name": "PRCXI_nest_1_troughplate4",
        "uuid": "bd6f7d88-e96a-4efa-b8c6-f5514aeb7cdf",
    },
    "ctac": {
        "id": "/gn_workstation/PRCXI/PRCXI_Deck/PRCXI_nest_1_troughplate2",
        "name": "PRCXI_nest_1_troughplate2",
        "uuid": "8e1cb526-32c4-4c6c-bb22-6faa81e209c5",
    },
    "waste": {
        "id": "/gn_workstation/PRCXI/PRCXI_Deck/PRCXI_nest_1_troughplate5",
        "name": "PRCXI_nest_1_troughplate5",
        "uuid": "53aca277-6dea-43a5-89d2-83b15a4a01b7",
    },
    "tips1": {
        "id": "/gn_workstation/PRCXI/PRCXI_Deck/PRCXI_1250uL_Tips1",
        "name": "PRCXI_1250uL_Tips1",
        "uuid": "a2c7801f-bef0-4007-b650-73ad32f04778",
    },
    "tips2": {
        "id": "/gn_workstation/PRCXI/PRCXI_Deck/PRCXI_1250uL_Tips2",
        "name": "PRCXI_1250uL_Tips2",
        "uuid": "e7df077f-ec56-48e6-a7af-9fabf569c418",
    },
    "tips3": {
        "id": "/gn_workstation/PRCXI/PRCXI_Deck/PRCXI_1250uL_Tips3",
        "name": "PRCXI_1250uL_Tips3",
        "uuid": "bf3f445a-839e-4e20-8dd8-40aa5a147807",
    },
}

# 固体加样：0.1mg 单位
WEIGHT_CTAC = 1820   # 182 mg
WEIGHT_CTAB = 1600   # 160 mg
WEIGHT_AA = 2113     # 211.3 mg

# 9320 子图 group uuid
GROUP_9320 = "4b31d684-25dc-475f-90e0-5b345194c1b8"
GROUP_TIPS = "078ea7eb-6b4b-4b02-9c8a-5656fd142c51"

# 固体加样段锚点
STEP1_SETUP = "e0543c53-ab5f-4679-9694-28bd66aaa940"
STEP1_TEARDOWN = "b7847e7a-fd66-4ea5-aeb7-1f54637dd7a2"
STEP8_SETUP = "72712f85-7078-4904-a9de-f439eed3c9c5"
STEP8_TEARDOWN = "02d41ed5-f1ae-4f77-b84f-eaf280759f9b"

# 主流程锚点
ANCHOR = {
    "step1_place_9320": "2bd5b12c-abeb-48c7-a1b3-55fe9b0454cb",
    "step5_pick_stack4": "8a505fab-2286-400b-aa4a-f63b1195fa70",
    "step8_pick_after_tips": "5bc4938d-3210-4c68-a2db-0d18463d709c",
    "step8_place_solid": "c0f4dd10-3587-44dc-9396-da749e4494c0",
    "step8_place_9320": "a8968ef9-1efc-4a17-8207-2c54536ceac7",
    "legacy_skip_place": "383eca50-0945-4bbf-920f-dcba5e2e4865",
}

SOLID_CMD_TEMPLATE = "c7c5eb46-b3d5-404f-a24a-8639f894013d"
CREATE_TEMPLATE = "cc5476e6-7c2e-40f7-878f-c7903ba16a3b"
RUN_TEMPLATE = "d10989c8-6f47-4328-bec1-bfe911c6dfc1"
PICK_TEMPLATE = "39450241-0ccb-4017-9a05-e042fce66c77"
PLACE_TEMPLATE = "5cd348b5-31d1-4ffc-841d-18672f6d26ed"

# PositionInfo 固体加样标定
BUCKET_X = {"CTAC": -4272, "CTAB": -3682, "AA": -3092}
DISPENSE_X0 = -625
DISPENSE_Y = 660
DISPENSE_Z = 143000
BUCKET_TAKE_Y = 2200
BUCKET_TAKE_Z = 234000
DOSE_ROUNDS = 8
X_STEP = 9

# 保留的节点 uuid（复用/更新）
UID = {
    "create_a": "8116aabc-c6d7-4ecb-936b-595f29607013",
    "run_a": "6c65f330-ee22-4dd9-93c5-2e3adf9c8945",
    "create_b": "f25a2e14-74d2-4936-a30b-db140ad42527",
    "run_b": "36e5462f-9d7d-4645-bc40-79a76d01fe5c",
    "create_c": "cb122eab-f389-43e7-a903-f512faad1a1e",
    "run_c": "64f874a5-b204-425b-9302-3bebc08d3fb8",
}


def _edge(edges: list, src: str, tgt: str) -> None:
    edges.append(
        {
            "source_node_uuid": src,
            "target_node_uuid": tgt,
            "source_handle_key": "ready",
            "source_handle_io": "source",
            "target_handle_key": "ready",
            "target_handle_io": "target",
        }
    )


def _remove_edges_for(nodes_to_remove: set[str], edges: list[dict]) -> None:
    edges[:] = [
        e
        for e in edges
        if e["source_node_uuid"] not in nodes_to_remove and e["target_node_uuid"] not in nodes_to_remove
    ]


def _replace_edge(edges: list[dict], src: str, old_tgt: str | None, new_tgt: str) -> None:
    if old_tgt:
        edges[:] = [
            e
            for e in edges
            if not (e["source_node_uuid"] == src and e["target_node_uuid"] == old_tgt)
        ]
    else:
        edges[:] = [e for e in edges if e["source_node_uuid"] != src]
    _edge(edges, src, new_tgt)


def _chain_between(edges: list[dict], start: str, end: str) -> list[str]:
    by_src: dict[str, str] = {}
    for e in edges:
        if e["source_node_uuid"] not in by_src:
            by_src[e["source_node_uuid"]] = e["target_node_uuid"]
    cur = by_src.get(start)
    mid: list[str] = []
    while cur and cur != end:
        mid.append(cur)
        cur = by_src.get(cur)
    return mid


def _solid_cmd(
    footer: str,
    cmd_type: int,
    x_pos: int,
    y_pos: int,
    material_z_pos: int = 0,
    volune_weight: int = 0,
    *,
    door_pos: int = 0,
    timeout: float = 180,
) -> dict[str, Any]:
    node = _base_ilab("", "auto-execute_command", footer, 0, 0)
    node["device_name"] = "gn_solid_weighing"
    node["template_uuid"] = SOLID_CMD_TEMPLATE
    node["resource_name"] = "gn_solid_weighing"
    speed = 500 if cmd_type in (10, 11, 18) else 300
    node["param"] = {
        "x_pos": x_pos,
        "y_pos": y_pos,
        "timeout": 600 if cmd_type == 11 else timeout,
        "x_speed": speed,
        "y_speed": speed,
        "cmd_type": cmd_type,
        "door_pos": door_pos if cmd_type == 11 else 0,
        "door_speed": 150 if cmd_type == 11 else 0,
        "gripper_z_pos": 0,
        "volune_weight": volune_weight,
        "material_z_pos": material_z_pos,
        "gripper_z_speed": 0,
        "material_z_speed": 300 if cmd_type == 11 else 0,
    }
    return node


def _protocol_node(uid: str, parent: str, name: str, footer: str, x: float, y: float, template: str) -> dict[str, Any]:
    node = _base_ilab(parent, name, footer, x, y)
    node["uuid"] = uid
    node["template_uuid"] = template
    if name == "auto-create_protocol":
        node["param"] = {
            "none_keys": [],
            "protocol_date": "",
            "protocol_name": "",
            "protocol_type": "",
            "protocol_author": "",
            "protocol_version": "",
            "protocol_description": "",
        }
    else:
        node["param"] = {"protocol_id": None}
    return node


def _robot_node(name: str, footer: str, station: str, number: int, item_type: str = "plate") -> dict[str, Any]:
    node = _base_ilab("", name, footer, 0, 0)
    node["device_name"] = "gn_robotic_arm"
    node["resource_name"] = "gn_robotic_arm"
    node["template_uuid"] = PICK_TEMPLATE if name == "pick" else PLACE_TEMPLATE
    node["param"] = {
        "number": number,
        "station": station,
        "timeout": 30000,
        "x_speed": 400,
        "item_type": item_type,
    }
    return node


def _build_ctac_ctab_round(col: int) -> list[dict[str, Any]]:
    x_disp = DISPENSE_X0 + col * X_STEP
    r = col + 1
    return [
        _solid_cmd(
            f"Step1-R{r}: 取1号桶CTAC",
            10,
            BUCKET_X["CTAC"],
            BUCKET_TAKE_Y,
            BUCKET_TAKE_Z,
        ),
        _solid_cmd(
            f"Step1-R{r}: 加CTAC 182mg @X{x_disp}",
            11,
            x_disp,
            DISPENSE_Y,
            DISPENSE_Z,
            WEIGHT_CTAC,
            door_pos=3600,
        ),
        _solid_cmd(
            f"Step1-R{r}: 放回1号桶CTAC",
            18,
            BUCKET_X["CTAC"],
            BUCKET_TAKE_Y,
            BUCKET_TAKE_Z,
        ),
        _solid_cmd(
            f"Step1-R{r}: 取2号桶CTAB",
            10,
            BUCKET_X["CTAB"],
            BUCKET_TAKE_Y,
            BUCKET_TAKE_Z,
        ),
        _solid_cmd(
            f"Step1-R{r}: 加CTAB 160mg @X{x_disp}",
            11,
            x_disp,
            DISPENSE_Y,
            DISPENSE_Z,
            WEIGHT_CTAB,
            door_pos=3600,
        ),
        _solid_cmd(
            f"Step1-R{r}: 放回2号桶CTAB",
            18,
            BUCKET_X["CTAB"],
            BUCKET_TAKE_Y,
            BUCKET_TAKE_Z,
        ),
    ]


def _build_aa_round(col: int) -> list[dict[str, Any]]:
    x_disp = DISPENSE_X0 + col * X_STEP
    r = col + 1
    return [
        _solid_cmd(
            f"Step8-R{r}: 取3号桶AA",
            10,
            BUCKET_X["AA"],
            BUCKET_TAKE_Y,
            BUCKET_TAKE_Z,
        ),
        _solid_cmd(
            f"Step8-R{r}: 加AA 211.34mg @X{x_disp}",
            11,
            x_disp,
            DISPENSE_Y,
            DISPENSE_Z,
            WEIGHT_AA,
            door_pos=3600,
        ),
        _solid_cmd(
            f"Step8-R{r}: 放回3号桶AA",
            18,
            BUCKET_X["AA"],
            BUCKET_TAKE_Y,
            BUCKET_TAKE_Z,
        ),
    ]


def expand_solid_dose_loops(nodes: list[dict], edges: list[dict]) -> None:
    """Step1 / Step8 固体加样各展开 8 轮。"""
    for setup, teardown, builder in (
        (STEP1_SETUP, STEP1_TEARDOWN, _build_ctac_ctab_round),
        (STEP8_SETUP, STEP8_TEARDOWN, _build_aa_round),
    ):
        middle = _chain_between(edges, setup, teardown)
        if not middle:
            continue
        remove = set(middle)
        nodes[:] = [n for n in nodes if n["uuid"] not in remove]
        _remove_edges_for(remove, edges)

        new_chain: list[dict[str, Any]] = []
        for col in range(DOSE_ROUNDS):
            new_chain.extend(builder(col))

        for i, n in enumerate(new_chain):
            n["pose"]["position"]["x"] = 500 + i * 40
            n["pose"]["position"]["y"] = -2200

        nodes.extend(new_chain)
        uuids = [n["uuid"] for n in new_chain]
        _replace_edge(edges, setup, None, uuids[0])
        for a, b in zip(uuids, uuids[1:]):
            _edge(edges, a, b)
        _edge(edges, uuids[-1], teardown)


def _move_tip_rack(parent: str, footer: str, to_slot: int, tip_res: dict, x: float) -> dict[str, Any]:
    node = _move_plate(parent, footer, tip_res, 16, to_slot, x, 30)
    node["param"]["put_down_position"] = 2
    node["param"]["pinch_it_up_position"] = 2
    return node


def rebuild_tip_group(nodes: list[dict], edges: list[dict]) -> tuple[str, str]:
    """Step7: stack 71/72/73 -> 9320 -> T11/T12/T15。返回 (入口 pick, 出口 run) uuid。"""
    children = [n for n in nodes if n.get("parent_uuid") == GROUP_TIPS]
    remove = {n["uuid"] for n in children}
    nodes[:] = [n for n in nodes if n["uuid"] not in remove]
    _remove_edges_for(remove, edges)

    tip_configs = [
        (71, 11, RES["tips1"], "Step7a: 堆栈71号枪头盒 -> T11"),
        (72, 12, RES["tips3"], "Step7b: 堆栈72号枪头盒 -> T12"),
        (73, 15, RES["tips2"], "Step7c: 堆栈73号枪头盒 -> T15"),
    ]

    entry_pick = ""
    exit_run = ""
    prev_run = ""

    for i, (stack_no, slot, tip_res, label) in enumerate(tip_configs):
        y_base = 20 + i * 120
        pick = _robot_node("pick", f"{label} (pick)", "stack", stack_no, "plate")
        pick["parent_uuid"] = GROUP_TIPS
        pick["pose"]["position"] = {"x": 15, "y": y_base, "z": 0}

        place = _robot_node("place", f"{label} (place 9320)", "nine9320", 1, "plate")
        place["parent_uuid"] = GROUP_TIPS
        place["pose"]["position"] = {"x": 320, "y": y_base, "z": 0}

        create = _protocol_node(str(uuid.uuid4()), GROUP_TIPS, "auto-create_protocol", f"{label} 移液", 620, y_base, CREATE_TEMPLATE)
        move = _move_tip_rack(GROUP_TIPS, f"{label} move T16->T{slot}", slot, tip_res, 920 + i * 10)
        run = _protocol_node(str(uuid.uuid4()), GROUP_TIPS, "auto-run_protocol", f"执行 {label}", 1220, y_base, RUN_TEMPLATE)

        chain = [pick, place, create, move, run]
        nodes.extend(chain)

        _edge(edges, pick["uuid"], place["uuid"])
        _edge(edges, place["uuid"], create["uuid"])
        _edge(edges, create["uuid"], move["uuid"])
        _edge(edges, move["uuid"], run["uuid"])

        if i == 0:
            entry_pick = pick["uuid"]
        exit_run = run["uuid"]
        if prev_run:
            _edge(edges, prev_run, pick["uuid"])
        prev_run = run["uuid"]

    return entry_pick, exit_run


def ensure_step5_place_9320(nodes: list[dict], edges: list[dict]) -> str:
    """Step5: stack4 pick 后 place 到 9320（不再进离心机）。"""
    pick_uid = ANCHOR["step5_pick_stack4"]
    existing = next(
        (
            n
            for n in nodes
            if n.get("name") == "place"
            and n.get("device_name") == "gn_robotic_arm"
            and (n.get("param") or {}).get("station") == "nine9320"
            and (n.get("footer") or "").startswith("Step5")
        ),
        None,
    )
    if existing:
        place_uid = existing["uuid"]
    else:
        place = _robot_node("place", "Step5: 堆栈4号板放至9320(T6)", "nine9320", 1, "plate")
        nodes.append(place)
        place_uid = place["uuid"]
        _replace_edge(edges, pick_uid, None, place_uid)
    return place_uid


def fix_step8_shortcuts(edges: list[dict]) -> None:
    """Step8 必须经固体加样 8 轮后再 place 9320 -> 协议 C，删除旧捷径。"""
    _replace_edge(
        edges,
        ANCHOR["step8_pick_after_tips"],
        ANCHOR["legacy_skip_place"],
        ANCHOR["step8_place_solid"],
    )
    edges[:] = [
        e
        for e in edges
        if not (
            e["source_node_uuid"] == ANCHOR["legacy_skip_place"]
            or e["source_node_uuid"] == "959c89f1-dcf8-4dbb-a39b-9a814b77adaf"
            or e["target_node_uuid"] == "959c89f1-dcf8-4dbb-a39b-9a814b77adaf"
            or e["source_node_uuid"] == "7d80200d-6fd4-4cbf-be67-5d2efdc33a87"
            or e["target_node_uuid"] == "7d80200d-6fd4-4cbf-be67-5d2efdc33a87"
        )
    ]
    _replace_edge(edges, ANCHOR["step8_place_9320"], None, UID["create_c"])


def rewire_main_sequence(nodes: list[dict], edges: list[dict], tip_entry: str, tip_exit: str, step5_place: str) -> None:
    """串联 Step1 -> 9320-A -> Step5 -> 9320-B -> Step7 -> Step8 -> 9320-C。"""
    _replace_edge(edges, ANCHOR["step1_place_9320"], None, UID["create_a"])
    _replace_edge(edges, UID["run_a"], None, ANCHOR["step5_pick_stack4"])
    _replace_edge(edges, step5_place, None, UID["create_b"])
    _replace_edge(edges, UID["run_b"], None, tip_entry)
    _replace_edge(edges, tip_exit, None, ANCHOR["step8_pick_after_tips"])
    fix_step8_shortcuts(edges)

    labels = {
        UID["create_a"]: "9320协议A（Step2-4 震荡）",
        UID["run_a"]: "执行9320协议A",
        UID["create_b"]: "9320协议B（Step6 滴注）",
        UID["run_b"]: "执行9320协议B",
        UID["create_c"]: "9320协议C（Step9-11+静置）",
        UID["run_c"]: "执行9320协议C",
    }
    for n in nodes:
        if n["uuid"] in labels:
            n["footer"] = labels[n["uuid"]]
            n["parent_uuid"] = GROUP_9320


def _base_ilab(parent: str, name: str, footer: str, x: float, y: float) -> dict[str, Any]:
    return {
        "uuid": str(uuid.uuid4()),
        "parent_uuid": parent,
        "name": name,
        "type": "ILab",
        "icon": "",
        "pose": {
            "layout": "x-y",
            "position": {"x": x, "y": y, "z": 0},
            "position_3d": {"x": 0, "y": 0, "z": 0},
            "size": {"width": 0, "height": 0, "depth": 0},
            "scale": {"x": 1, "y": 1, "z": 1},
            "rotation": {"x": 0, "y": 0, "z": 0},
            "extra": None,
            "cross_section_type": "rectangle",
        },
        "param": None,
        "footer": footer,
        "device_name": "PRCXI",
        "disabled": False,
        "minimized": False,
        "lab_node_type": "Device",
        "template_uuid": "",
        "template_name": name,
        "resource_name": "liquid_handler.prcxi",
    }


def _transfer(
    parent: str,
    footer: str,
    sources: list,
    targets: list,
    source_slot: int,
    target_slot: int,
    tip_rack,
    tip_slot: int,
    asp_vols,
    dis_vols,
    is_96: bool = True,
    x: float = 300,
    y: float = 100,
    delays=None,
    asp_flow=100,
    dis_flow=100,
) -> dict[str, Any]:
    node = _base_ilab(parent, "transfer_liquid", footer, x, y)
    node["template_uuid"] = "b1f8991e-9f06-4abf-ad40-38406c5e5b0a"
    node["param"] = {
        "delays": delays if delays is not None else [],
        "spread": "wide",
        "offsets": [],
        "sources": sources,
        "targets": targets,
        "asp_vols": asp_vols,
        "dis_vols": dis_vols,
        "mix_stage": "none",
        "none_keys": [],
        "tip_racks": [tip_rack],
        "touch_tip": False,
        "is_96_well": is_96,
        "source_slots": source_slot,
        "target_slots": target_slot,
        "use_channels": [],
        "liquid_height": [],
        "asp_flow_rates": [asp_flow] if isinstance(asp_flow, (int, float)) else asp_flow,
        "dis_flow_rates": [dis_flow] if isinstance(dis_flow, (int, float)) else dis_flow,
        "tip_rack_slots": tip_slot,
        "blow_out_air_volume": [],
        "blow_out_air_volume_before": [],
    }
    return node


def _move_plate(
    parent: str,
    footer: str,
    plate_res: dict,
    from_slot: int,
    to_slot: int,
    x: float,
    y: float,
) -> dict[str, Any]:
    node = _base_ilab(parent, "move_plate", footer, x, y)
    node["template_uuid"] = "d91cb9b6-c85f-4982-945c-2c5cd1297da1"
    node["param"] = {
        "to": to_slot,
        "from": from_slot,
        "force": 5,
        "plate": [plate_res],
        "hierarchy": 1,
        "get_direction": "",
        "pickup_offset": {"x": 0, "y": 0, "z": 0},
        "put_direction": "",
        "drop_direction": "",
        "resource_offset": {"x": 0, "y": 0, "z": 0},
        "pickup_direction": "",
        "destination_offset": {"x": 0, "y": 0, "z": 0},
        "intermediate_locations": [],
        "pickup_distance_from_top": 0,
    }
    return node


SHAKE_TEMPLATE = "16a27598-6607-4cad-b6d4-a8573b3d2f54"


def _shaking(parent: str, uid: str, footer: str, time_min: int, temp: int, amplitude: int, x: float, y: float) -> dict[str, Any]:
    node = _base_ilab(parent, "auto-shaking_incubation_action", footer, x, y)
    node["uuid"] = uid
    node["template_uuid"] = SHAKE_TEMPLATE
    node["param"] = {
        "time": time_min,
        "is_wait": True,
        "amplitude": amplitude,
        "module_no": 1,
        "temperature": temp,
    }
    return node


def rebuild_9320_group(nodes: list[dict], edges: list[dict]) -> None:
    """重建 9320 移液站子图，拆为协议 A/B/C 三段。"""
    group_children = [n for n in nodes if n.get("parent_uuid") == GROUP_9320]
    keep_uuids = set(UID.values())
    remove_uuids = {n["uuid"] for n in group_children if n["uuid"] not in keep_uuids}
    nodes[:] = [n for n in nodes if n["uuid"] not in remove_uuids]

    internal = {n["uuid"] for n in group_children}
    edges[:] = [
        e
        for e in edges
        if not (e["source_node_uuid"] in internal and e["target_node_uuid"] in internal)
    ]

    # 确保 6 个 create/run 节点存在
    proto_defs = [
        (UID["create_a"], "auto-create_protocol", "9320协议A（Step2-4 震荡）", 15, 73),
        (UID["run_a"], "auto-run_protocol", "执行9320协议A", 1260, 73),
        (UID["create_b"], "auto-create_protocol", "9320协议B（Step6 滴注）", 15, 200),
        (UID["run_b"], "auto-run_protocol", "执行9320协议B", 620, 200),
        (UID["create_c"], "auto-create_protocol", "9320协议C（Step9-11+静置）", 15, 330),
        (UID["run_c"], "auto-run_protocol", "执行9320协议C", 1260, 330),
    ]
    existing = {n["uuid"] for n in nodes}
    for uid, name, footer, x, y in proto_defs:
        if uid not in existing:
            tpl = CREATE_TEMPLATE if name == "auto-create_protocol" else RUN_TEMPLATE
            nodes.append(_protocol_node(uid, GROUP_9320, name, footer, x, y, tpl))
        else:
            for n in nodes:
                if n["uuid"] == uid:
                    n["parent_uuid"] = GROUP_9320
                    n["footer"] = footer

    new_nodes: list[dict] = []

    # --- 协议 A: Step 2-4 ---
    move_t16_t14 = _move_plate(GROUP_9320, "Step2: T16->T14", RES["mix_plate"], 16, 14, 60, 130)
    water_t14 = _transfer(
        GROUP_9320, "Step2: T7 加水20ml->T14，枪头T15",
        [RES["water"]], [RES["mix_plate"]], 7, 14, RES["tips2"], 15,
        [20000], [20000], is_96=False, x=120, y=180, asp_flow=300, dis_flow=300,
    )
    move_t14_t3 = _move_plate(GROUP_9320, "Step3: T14->T3", RES["mix_plate"], 14, 3, 180, 130)
    shake = _shaking(GROUP_9320, str(uuid.uuid4()), "Step3: 震荡 500rpm 35°C 15min", 15, 35, 500, 240, 130)
    move_t3_t14 = _move_plate(GROUP_9320, "Step4: T3->T14", RES["mix_plate"], 3, 14, 300, 130)

    # --- 协议 B: Step 6 ---
    drip_delays = [180] * 162
    drip_asp, drip_dis = [], []
    for _ in range(81):
        drip_asp.extend([300, 4.8])
        drip_dis.extend([0, 4.8])
    drip_t6_t10 = _transfer(
        GROUP_9320, "Step6: T6滴注390ul->T10(81×)，枪头T11",
        [RES["stack4_plate"]], [RES["reaction_plate"]], 6, 10, RES["tips1"], 11,
        drip_asp, drip_dis, is_96=True, x=420, y=180, delays=drip_delays, asp_flow=50, dis_flow=50,
    )

    # --- 协议 C: Step 9-11 + 静置 + 离心前转板 ---
    move_t16_t3 = _move_plate(GROUP_9320, "Step9: T16->T3", RES["reaction_plate"], 16, 3, 540, 130)
    water_t3 = _transfer(
        GROUP_9320, "Step9: T7 加水20ml->T3，枪头T15",
        [RES["water"]], [RES["reaction_plate"]], 7, 3, RES["tips2"], 15,
        [20000], [20000], is_96=False, x=660, y=180, asp_flow=300, dis_flow=300,
    )
    t3_t10 = _transfer(
        GROUP_9320, "Step10: T3 390ul->T10，枪头T12",
        [RES["reaction_plate"]], [RES["reaction_plate"]], 3, 10, RES["tips3"], 12,
        [390], [390], is_96=True, x=780, y=180,
    )
    t14_t10 = _transfer(
        GROUP_9320, "Step11: T14 780ul->T10，枪头T11",
        [RES["mix_plate"]], [RES["reaction_plate"]], 14, 10, RES["tips1"], 11,
        [780], [780], is_96=True, x=900, y=180,
    )
    incubate = _shaking(GROUP_9320, str(uuid.uuid4()), "9320恒温静置 35°C 10min", 10, 35, 1, 1020, 130)
    move_t10_t16 = _move_plate(GROUP_9320, "离心前: T10->T16", RES["reaction_plate"], 10, 16, 1140, 130)

    seg_a = [move_t16_t14, water_t14, move_t14_t3, shake, move_t3_t14]
    seg_b = [drip_t6_t10]
    seg_c = [move_t16_t3, water_t3, t3_t10, t14_t10, incubate, move_t10_t16]
    new_nodes.extend(seg_a + seg_b + seg_c)
    nodes.extend(new_nodes)

    def wire(seg_nodes: list[dict], create: str, run: str) -> None:
        ids = [n["uuid"] for n in seg_nodes]
        _edge(edges, create, ids[0])
        for a, b in zip(ids, ids[1:]):
            _edge(edges, a, b)
        _edge(edges, ids[-1], run)

    wire(seg_a, UID["create_a"], UID["run_a"])
    wire(seg_b, UID["create_b"], UID["run_b"])
    wire(seg_c, UID["create_c"], UID["run_c"])


def patch_solid_and_stack(nodes: list[dict]) -> None:
    """更新堆栈 pick 编号、place 描述。"""
    for n in nodes:
        p = n.get("param") or {}
        footer = n.get("footer") or ""

        if n.get("name") == "pick" and p.get("station") == "stack":
            num = p.get("number")
            if "CTAC" in footer or "CTAB" in footer or (num == 1 and "Step1" in footer):
                p["number"] = 1
                n["footer"] = "Step1: 堆栈1号位取96孔板至固体加样(CTAC+CTAB)"
            elif num == 4 or "Step5" in footer:
                p["number"] = 4
                n["footer"] = "Step5: 堆栈4号位取96孔板至9320(T6)"
            elif num == 2 or "Step8" in footer:
                p["number"] = 2
                n["footer"] = "Step8: 堆栈2号位取板至固体加样(AA)"

        if n.get("name") == "place" and p.get("station") == "solid":
            if "固体" in footer or "Step" in footer:
                n["footer"] = "机械臂-放入固体加样仪(96孔板1号板位)"


def patch_centrifuge_and_supernatant(nodes: list[dict]) -> None:
    """离心参数 + 三次去上清 + CTAC 源位 T8。"""
    supernatant_volumes = [
        (1400, 500, "第1次离心: 吸1400ul 放500ul"),
        (500, 500, "第2次离心: 吸500ul 放500ul"),
        (500, 500, "第3次离心: 吸500ul 放500ul"),
    ]
    supernatant_idx = 0

    for n in nodes:
        p = n.get("param") or {}
        footer = n.get("footer") or ""

        # 离心机转速/时间
        if n.get("name") == "auto-execute_command" and n.get("device_name") == "gn_centrifuge":
            if p.get("cmd_type") == 6 and "离心" in footer:
                p["rpm"] = 4000
                p["time_minutes"] = 30

        # CTAC 加液：T10 -> T8
        if n.get("name") == "transfer_liquid":
            sources = p.get("sources") or []
            is_ctac = any(
                "troughplate2" in (s.get("name") or "") or "troughplate2" in (s.get("id") or "")
                for s in sources
            )
            if is_ctac or p.get("source_slots") == 10:
                p["source_slots"] = 8
                if "T10" in footer:
                    n["footer"] = footer.replace("T10", "T8")

            # 去上清液
            if "上清" in footer or "去上清" in footer:
                if supernatant_idx < len(supernatant_volumes):
                    asp, dis, desc = supernatant_volumes[supernatant_idx]
                    p["asp_vols"] = [asp]
                    p["dis_vols"] = [dis]
                    n["footer"] = f"T16->T5 {desc}，枪头T12"
                    supernatant_idx += 1

            # 第2次离心后取 150ul 到 T4
            if supernatant_idx == 2 and "第2次" in footer:
                pass  # 需单独节点，见下


def add_t4_sample_node(nodes: list[dict], edges: list[dict]) -> None:
    """第2次离心后取 150ul 到 T4（去重）。"""
    if any("取150ul到T4" in (n.get("footer") or "") for n in nodes):
        return
    sample = _transfer(
        "",
        "第2次离心后: 取150ul到T4",
        [RES["reaction_plate"]],
        [RES["waste"]],
        16,
        4,
        RES["tips3"],
        12,
        [150],
        [150],
        is_96=True,
        x=4000,
        y=6200,
    )
    nodes.append(sample)


def patch(data: dict) -> dict:
    data = copy.deepcopy(data)
    nodes = data["data"]["nodes"]
    edges = data["data"]["edges"]

    expand_solid_dose_loops(nodes, edges)
    rebuild_9320_group(nodes, edges)
    tip_entry, tip_exit = rebuild_tip_group(nodes, edges)
    step5_place = ensure_step5_place_9320(nodes, edges)
    rewire_main_sequence(nodes, edges, tip_entry, tip_exit, step5_place)
    patch_solid_and_stack(nodes)
    patch_centrifuge_and_supernatant(nodes)
    add_t4_sample_node(nodes, edges)

    return data


def main() -> None:
    raw = json.loads(SRC.read_text(encoding="utf-8"))
    updated = patch(raw)
    text = json.dumps(updated, ensure_ascii=False, indent=2)
    OUT_DOWNLOADS.write_text(text, encoding="utf-8")
    OUT_REPO.write_text(text, encoding="utf-8")
    print(f"Patched workflow written to:\n  {OUT_DOWNLOADS}\n  {OUT_REPO}")

    solid11 = sum(
        1
        for n in updated["data"]["nodes"]
        if n.get("device_name") == "gn_solid_weighing" and (n.get("param") or {}).get("cmd_type") == 11
    )
    tips = [n for n in updated["data"]["nodes"] if n.get("parent_uuid") == GROUP_TIPS]
    g9320 = [n for n in updated["data"]["nodes"] if n.get("parent_uuid") == GROUP_9320]
    print(f"Solid dispense (cmd 11) nodes: {solid11} (Step1: 16 + Step8: 8)")
    print(f"Tip group nodes: {len(tips)} (expect 15)")
    print(f"9320 group nodes: {len(g9320)}")
    for n in g9320:
        if n.get("name") != "auto-run_protocol" and n.get("name") != "auto-create_protocol":
            print(f"  - {n.get('footer', n['name'])[:60]}")


if __name__ == "__main__":
    main()
