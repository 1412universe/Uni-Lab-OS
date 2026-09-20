"""Rebuild 滴注81次 — flat node-link, same schema as 金纳米颗粒工作流 export."""
from __future__ import annotations

import argparse
import copy
import json
import uuid
from pathlib import Path
from typing import Any

OUT = Path(r"c:\Users\Administrator\Downloads\滴注81次.json")
REPO_OUT = Path(__file__).resolve().parent / "滴注81次.json"
TITRATION_OUT = Path(__file__).resolve().parent / "滴注81次_titration.json"

CREATE_UUID = "12a51262-10c5-4587-b722-76ebc418e522"
RUN_UUID = "80fde619-29c6-45f9-8806-871a08918772"

TIP_CONFIGS = [
    {
        "slot": 15,
        "label": "T15",
        "tip_racks": [
            {
                "id": "/gn_workstation/PRCXI/PRCXI_Deck/PRCXI_1250uL_Tips2",
                "name": "PRCXI_1250uL_Tips2",
                "uuid": "e7df077f-ec56-48e6-a7af-9fabf569c418",
            }
        ],
    },
    {
        "slot": 11,
        "label": "T11",
        "tip_racks": [
            {
                "id": "/gn_workstation/PRCXI/PRCXI_Deck/PRCXI_1250uL_Tips1",
                "name": "PRCXI_1250uL_Tips1",
                "uuid": "a2c7801f-bef0-4007-b650-73ad32f04778",
            }
        ],
    },
    {
        "slot": 12,
        "label": "T12",
        "tip_racks": [
            {
                "id": "/gn_workstation/PRCXI/PRCXI_Deck/PRCXI_1250uL_Tips3",
                "name": "PRCXI_1250uL_Tips3",
                "uuid": "bf3f445a-839e-4e20-8dd8-40aa5a147807",
            }
        ],
    },
]

TRANSFER_PARAM: dict[str, Any] = {
    "delays": [],
    "spread": "wide",
    "offsets": [],
    "sources": [
        {
            "id": "/gn_workstation/PRCXI/PRCXI_Deck/PRCXI_nest_1_troughplate3",
            "name": "PRCXI_nest_1_troughplate3",
            "uuid": "dd9a0925-8aa7-44c0-a1bc-7dc9776ee6e0",
        }
    ],
    "targets": [
        {
            "id": "/gn_workstation/PRCXI/PRCXI_Deck/PRCXI_nest_1_troughplate2",
            "name": "PRCXI_nest_1_troughplate2",
            "uuid": "8e1cb526-32c4-4c6c-bb22-6faa81e209c5",
        }
    ],
    "asp_vols": [4.8],
    "dis_vols": [4.8],
    "mix_stage": "none",
    "none_keys": [],
    "tip_racks": TIP_CONFIGS[0]["tip_racks"],
    "touch_tip": False,
    "is_96_well": False,
    "source_slots": 6,
    "target_slots": 10,
    "use_channels": [0],
    "liquid_height": [10],
    "asp_flow_rates": [50],
    "dis_flow_rates": [1],
    "dispensing_method": ["BottleNeck"],
    "hover_below_liquid_level": [10],
    "z_start_point_offset_height": [8],
    "tip_rack_slots": 15,
    "post_air_volume": [],
    "blow_out_air_volume": [],
    "blow_out_air_volume_before": [300],
}

TITRATION_PARAM: dict[str, Any] = {
    "offsets": [],
    "sources": TRANSFER_PARAM["sources"],
    "targets": TRANSFER_PARAM["targets"],
    "asp_vols": [300, 4.8],
    "dis_vols": [0, 4.8],
    "none_keys": [],
    "tip_racks": TIP_CONFIGS[0]["tip_racks"],
    "is_96_well": True,
    "source_slots": 6,
    "target_slots": 10,
    "use_channels": [],
    "liquid_height": [10],
    "asp_flow_rates": [50],
    "dis_flow_rates": [1],
    "dispensing_method": ["BottleNeck"],
    "hover_below_liquid_level": [10],
    "z_start_point_offset_height": [8],
    "tip_rack_slots": 15,
    "post_air_volume": [],
    "blow_out_air_volume": [],
    "blow_out_air_volume_before": [300],
    "repeat_count": 81,
    "cycle_delay_s": 0,
}

# titration_liquid 版：按枪头盒分段（30+30+21），每段一个节点
TITRATION_BATCHES = [
    (30, TIP_CONFIGS[0]),
    (30, TIP_CONFIGS[1]),
    (21, TIP_CONFIGS[2]),
]


def _pose(x: float, y: float) -> dict[str, Any]:
    return {
        "layout": "x-y",
        "position": {"x": x, "y": y, "z": 0},
        "position_3d": {"x": 0, "y": 0, "z": 0},
        "size": {"width": 0, "height": 0, "depth": 0},
        "scale": {"x": 1, "y": 1, "z": 1},
        "rotation": {"x": 0, "y": 0, "z": 0},
        "extra": None,
        "cross_section_type": "rectangle",
    }


def _edge(src: str, dst: str) -> dict[str, str]:
    return {
        "source_node_uuid": src,
        "target_node_uuid": dst,
        "source_handle_key": "ready",
        "source_handle_io": "source",
        "target_handle_key": "ready",
        "target_handle_io": "target",
    }


def _device_node(
    node_uuid: str,
    template_name: str,
    template_uuid: str,
    param: Any,
    footer: str,
    x: float,
    y: float,
) -> dict[str, Any]:
    return {
        "uuid": node_uuid,
        "parent_uuid": "",
        "name": template_name,
        "type": "ILab",
        "icon": "",
        "pose": _pose(x, y),
        "param": param,
        "footer": footer,
        "device_name": "PRCXI",
        "disabled": False,
        "minimized": False,
        "lab_node_type": "Device",
        "template_uuid": template_uuid,
        "template_name": template_name,
        "resource_name": "liquid_handler.prcxi",
    }


def build() -> dict[str, Any]:
    workflow_uuid = str(uuid.uuid4())
    nodes: list[dict[str, Any]] = []
    transfer_uuids: list[str] = []

    # 与金纳米颗粒工作流相同：全扁平、parent_uuid 为空、横向串联
    x_start, y_base, x_step = 350.0, 250.0, 200.0

    nodes.append(
        _device_node(
            CREATE_UUID,
            "auto-create_protocol",
            "cc5476e6-7c2e-40f7-878f-c7903ba16a3b",
            {
                "none_keys": [],
                "protocol_date": "",
                "protocol_name": "滴注81次",
                "protocol_type": "",
                "protocol_author": "",
                "protocol_version": "",
                "protocol_description": "",
            },
            "创建PRCXI协议",
            x_start,
            y_base,
        )
    )

    for i in range(1, 82):
        batch_idx = (i - 1) // 30
        tip_cfg = TIP_CONFIGS[min(batch_idx, 2)]
        node_uuid = str(uuid.uuid4())
        transfer_uuids.append(node_uuid)
        param = copy.deepcopy(TRANSFER_PARAM)
        param["tip_racks"] = copy.deepcopy(tip_cfg["tip_racks"])
        param["tip_rack_slots"] = tip_cfg["slot"]
        # 双行蛇形，避免 81 节点挤在一条极长线里难以看到
        row = (i - 1) // 27
        col = (i - 1) % 27
        x = x_start + (col + 1) * x_step
        y = y_base + row * 120.0
        nodes.append(
            _device_node(
                node_uuid,
                "transfer_liquid",
                "b1f8991e-9f06-4abf-ad40-38406c5e5b0a",
                param,
                f"滴注第{i}/81 枪头{tip_cfg['label']}",
                x,
                y,
            )
        )

    nodes.append(
        _device_node(
            RUN_UUID,
            "auto-run_protocol",
            "d10989c8-6f47-4328-bec1-bfe911c6dfc1",
            {"protocol_id": None},
            "执行PRCXI协议",
            x_start + 28 * x_step,
            y_base + 120.0,
        )
    )

    edges = [_edge(CREATE_UUID, transfer_uuids[0])]
    edges.extend(_edge(a, b) for a, b in zip(transfer_uuids, transfer_uuids[1:]))
    edges.append(_edge(transfer_uuids[-1], RUN_UUID))

    return {
        "target_lab_uuid": "803298d7-ba98-4cd5-a546-b7ca08b4cb12",
        "name": "滴注81次",
        "data": {
            "workflow_uuid": workflow_uuid,
            "workflow_name": "滴注81次",
            "nodes": nodes,
            "edges": edges,
        },
    }


def build_titration() -> dict[str, Any]:
    """titration_liquid 版：3 个节点（换枪头分段），替代 81 个 transfer_liquid 节点。"""
    workflow_uuid = str(uuid.uuid4())
    nodes: list[dict[str, Any]] = []
    titration_uuids: list[str] = []
    x_start, y_base, x_step = 350.0, 250.0, 280.0

    nodes.append(
        _device_node(
            CREATE_UUID,
            "auto-create_protocol",
            "cc5476e6-7c2e-40f7-878f-c7903ba16a3b",
            {
                "none_keys": [],
                "protocol_date": "",
                "protocol_name": "滴注81次_titration",
                "protocol_type": "",
                "protocol_author": "",
                "protocol_version": "",
                "protocol_description": "",
            },
            "创建PRCXI协议",
            x_start,
            y_base,
        )
    )

    for idx, (repeat_count, tip_cfg) in enumerate(TITRATION_BATCHES):
        node_uuid = str(uuid.uuid4())
        titration_uuids.append(node_uuid)
        param = copy.deepcopy(TITRATION_PARAM)
        param["repeat_count"] = repeat_count
        param["tip_racks"] = copy.deepcopy(tip_cfg["tip_racks"])
        param["tip_rack_slots"] = tip_cfg["slot"]
        nodes.append(
            _device_node(
                node_uuid,
                "titration_liquid",
                "",
                param,
                f"滴注段{idx + 1}/3 ×{repeat_count} 枪头{tip_cfg['label']}",
                x_start + (idx + 1) * x_step,
                y_base,
            )
        )

    nodes.append(
        _device_node(
            RUN_UUID,
            "auto-run_protocol",
            "d10989c8-6f47-4328-bec1-bfe911c6dfc1",
            {"protocol_id": None},
            "执行PRCXI协议",
            x_start + 4 * x_step,
            y_base,
        )
    )

    edges = [_edge(CREATE_UUID, titration_uuids[0])]
    edges.extend(_edge(a, b) for a, b in zip(titration_uuids, titration_uuids[1:]))
    edges.append(_edge(titration_uuids[-1], RUN_UUID))

    return {
        "target_lab_uuid": "803298d7-ba98-4cd5-a546-b7ca08b4cb12",
        "name": "滴注81次_titration",
        "data": {
            "workflow_uuid": workflow_uuid,
            "workflow_name": "滴注81次_titration",
            "nodes": nodes,
            "edges": edges,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="生成滴注81次工作流 JSON")
    parser.add_argument(
        "--titration",
        action="store_true",
        help="生成 titration_liquid 分段版（3 节点）而非 81 个 transfer_liquid",
    )
    args = parser.parse_args()

    if args.titration:
        doc = build_titration()
        text = json.dumps(doc, ensure_ascii=False, indent=2)
        TITRATION_OUT.write_text(text, encoding="utf-8")
        print(f"Wrote {TITRATION_OUT}")
    else:
        doc = build()
        text = json.dumps(doc, ensure_ascii=False, indent=2)
        OUT.write_text(text, encoding="utf-8")
        REPO_OUT.write_text(text, encoding="utf-8")
        print(f"Wrote {OUT}")

    n = len(doc["data"]["nodes"])
    e = len(doc["data"]["edges"])
    g = sum(1 for nd in doc["data"]["nodes"] if nd.get("type") == "Group")
    print(f"workflow_uuid={doc['data']['workflow_uuid']}")
    print(f"nodes={n} edges={e} groups={g}")


if __name__ == "__main__":
    main()
