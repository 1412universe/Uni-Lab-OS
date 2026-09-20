#!/usr/bin/env python3
"""将 legacy ``GN_station.json`` 转为 kernel-v2 ``community.gn_lab.*`` 启动图。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GN_ROOT = ROOT.parent
LEGACY = GN_ROOT / "GN_station.json"
OUT = ROOT / "deployment" / "graphs" / "gn-station.json"

import sys

sys.path.insert(0, str(ROOT))
from gn_lab.resources.decks import PRCXI_DECK_SITES

CLASS_MAP = {
    "liquid_handler.prcxi": "community.gn_lab.gn_prcxi",
}


def kernel_class(legacy: str) -> str:
    if not legacy:
        return legacy
    mapped = CLASS_MAP.get(legacy)
    if mapped:
        return mapped
    if legacy.startswith("community."):
        return legacy
    if legacy.startswith("gn_"):
        return f"community.gn_lab.{legacy}"
    return legacy


def main() -> None:
    payload = json.loads(LEGACY.read_text(encoding="utf-8"))
    nodes = payload.get("nodes") or []
    for node in nodes:
        if node.get("type") == "device" and isinstance(node.get("class"), str):
            node["class"] = kernel_class(node["class"])
            config = node.setdefault("config", {})
            if isinstance(config, dict) and node.get("class", "").startswith("community.gn_lab.gn_"):
                if node.get("id") != "PRCXI":
                    config.setdefault("auto_connect", True)
            if node.get("id") == "PRCXI" and isinstance(config, dict):
                config.pop("auto_connect", None)
        if node.get("id") == "PRCXI_Deck":
            node["class"] = "community.gn_lab.prcxi_deck"
            config = node.setdefault("config", {})
            if isinstance(config, dict) and not config.get("sites"):
                config["sites"] = PRCXI_DECK_SITES
    out_payload = {
        "metadata": {
            "purpose": "production",
            "owner": "gn-lab",
            "version": "0.1.0",
            "notes": "由 GN_station.json 转换；OPC CSV 相对路径在 GN 驱动目录解析。",
            "legacy_source": str(LEGACY.relative_to(GN_ROOT)),
        },
        "nodes": nodes,
        "links": payload.get("links") or [],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    dry = json.loads(json.dumps(out_payload))
    dry["metadata"]["purpose"] = "simulation"
    dry["metadata"]["notes"] = "dry-run：不连现场 OPC/PRCXI；workspace --runtime-mode dry-run 会把 action_mode 设为 simulate。"
    for node in dry.get("nodes") or []:
        config = node.get("config")
        if not isinstance(config, dict):
            continue
        if "auto_connect" in config:
            config["auto_connect"] = False
        if node.get("id") == "PRCXI":
            config["setup"] = False
            config["simulator"] = True
            config["debug"] = True
    dry_out = ROOT / "deployment" / "graphs" / "gn-station-dry-run.json"
    dry_out.write_text(json.dumps(dry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    alias = ROOT / "deployment" / "graphs" / "dry-run.json"
    alias.write_text(json.dumps(dry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(nodes)} nodes)")
    print(f"wrote {dry_out}")
    print(f"wrote {alias}")


if __name__ == "__main__":
    main()
