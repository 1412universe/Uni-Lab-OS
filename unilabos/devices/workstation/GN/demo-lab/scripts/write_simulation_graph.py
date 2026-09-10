#!/usr/bin/env python3
"""生成称量分装演示启动图。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "deployment" / "graphs"


def node(id, name, type, cls, parent=None, position=None, config=None, data=None, barcode=None):
    item = {"id": id, "name": name, "type": type, "class": cls, "parent": parent, "position": position or {"x": 0.0, "y": 0.0, "z": 0.0}, "config": config or {}, "data": data or {}}
    if barcode:
        item["barcode"] = barcode
    return item


def site(label, x, y, content_type, occupied_by=None):
    return {"index": label, "label": label, "position": {"x": x, "y": y, "z": 0.0}, "size": {"width": 90.0, "height": 90.0, "depth": 140.0}, "content_type": [content_type], "visible": True, "occupied_by": occupied_by}


def device(id, name, cls, x, y, sites=None):
    config = {"endpoint": "sim://local", "command_timeout_seconds": 120.0, "auto_connect": False}
    if sites:
        config["sites"] = sites
    return node(id, name, "device", cls, position={"x": x, "y": y, "z": 0.0}, config=config, data={"status": "offline"})


def container(id, name, cls, parent, barcode, category, size):
    return node(id, name, "container" if category != "tip_box" else "resource", cls, parent=parent, barcode=barcode, config={"size_x": size[0], "size_y": size[1], "size_z": size[2], "category": category}, data={"lifecycle_state": "available"})


def main() -> None:
    gripper = [{"index": "gripper", "label": "GRIPPER", "visible": False, "meta_data": {"unilab": {"resource_role": "robot.gripper"}}}]
    nodes = [
        node("main_deck", "主工作台", "deck", "community.demo_lab.main_deck", config={"setup": False, "size_x": 1800.0, "size_y": 900.0, "size_z": 20.0}),
        node("source_rack", "原料载架", "warehouse", "community.demo_lab.source_rack", parent="main_deck", position={"x": 80.0, "y": 80.0, "z": 20.0}, config={"sites": [site("S01", 10, 10, "stock_vial", "stock_vial_001"), site("E01", 110, 10, "aliquot_vial", "aliquot_vial_001")]}),
        node("tip_warehouse", "吸头仓", "warehouse", "community.demo_lab.tip_warehouse", parent="main_deck", position={"x": 360.0, "y": 80.0, "z": 20.0}, config={"sites": [site("T01", 10, 10, "tip_box", "tipbox_001")]}),
        node("used_rack", "已用母液区", "warehouse", "community.demo_lab.used_rack", parent="main_deck", position={"x": 80.0, "y": 320.0, "z": 20.0}, config={"sites": [site("U01", 10, 10, "stock_vial")]}),
        node("finished_rack", "成品区", "warehouse", "community.demo_lab.finished_rack", parent="main_deck", position={"x": 280.0, "y": 320.0, "z": 20.0}, config={"sites": [site("F01", 10, 10, "aliquot_vial")]}),
        device("transport_robot_01", "搬运机器人 1", "community.demo_lab.transport_robot_sim", 1100, 200, sites=gripper),
        device("balance_station_01", "天平工站 1", "community.demo_lab.balance_station_sim", 1300, 80),
        device("pipette_station_01", "移液工站 1", "community.demo_lab.pipette_station_sim", 1500, 80),
        device("cap_station_01", "封盖工站 1", "community.demo_lab.cap_station_sim", 1300, 320),
        node("balance_process_rack", "称量工艺位", "warehouse", "community.demo_lab.balance_process_rack", parent="balance_station_01", config={"logical_mount": True, "sites": [site("BAL1", 0, 0, "stock_vial")]}),
        node("pipette_process_rack", "移液工艺位", "warehouse", "community.demo_lab.pipette_process_rack", parent="pipette_station_01", config={"logical_mount": True, "sites": [site("PIP_S", 0, 0, "stock_vial"), site("PIP_T", 120, 0, "aliquot_vial")]}),
        node("cap_process_rack", "封盖工艺位", "warehouse", "community.demo_lab.cap_process_rack", parent="cap_station_01", config={"logical_mount": True, "sites": [site("CAP1", 0, 0, "aliquot_vial")]}),
        container("stock_vial_001", "标准样品母液 001", "community.demo_lab.stock_vial", "source_rack", "ASSAY-STOCK-001", "stock_vial", (56, 56, 105)),
        container("aliquot_vial_001", "空分装瓶 001", "community.demo_lab.aliquot_vial", "source_rack", "ASSAY-EMPTY-001", "aliquot_vial", (40, 40, 80)),
        container("tipbox_001", "吸头盒 001", "community.demo_lab.tip_box", "tip_warehouse", "ASSAY-TIPBOX-001", "tip_box", (86, 128, 136)),
    ]
    payload = {"metadata": {"purpose": "simulation", "owner": "demo-lab-demo", "version": "0.1.0", "notes": "标准样品称量分装演示。"}, "nodes": nodes, "links": []}
    OUT.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    (OUT / "simulation.json").write_text(text, encoding="utf-8")
    (OUT / "dry-run.json").write_text(text, encoding="utf-8")
    print(f"wrote {len(nodes)} nodes")


if __name__ == "__main__":
    main()
