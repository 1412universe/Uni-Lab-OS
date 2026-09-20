"""PRCXI 台面资源注册。"""

from __future__ import annotations

from unilabos.registry.decorators import resource

from unilabos.devices.workstation.GN.liquid_handling.prcxi.prcxi import (
    PRCXI9300Deck as _PRCXI9300Deck,
)

# T11/T12/T15 枪头盒；T1 废液槽；T2/T3/T6/T7/T8/T10/T14/T16 储液槽；T4 96 孔酶标板。
PRCXI_DECK_SITES = [
    {"index": "T1", "label": "T1", "position": {"x": 5.0, "y": 301.0, "z": 0.0}, "size": {"width": 128.0, "height": 86.0, "depth": 0.0}, "content_type": ["trash"], "visible": True},
    {"index": "T2", "label": "T2", "position": {"x": 142.5, "y": 301.0, "z": 0.0}, "size": {"width": 128.0, "height": 86.0, "depth": 0.0}, "content_type": ["trough"], "visible": True},
    {"index": "T3", "label": "T3", "position": {"x": 280.0, "y": 301.0, "z": 0.0}, "size": {"width": 128.0, "height": 86.0, "depth": 0.0}, "content_type": ["trough"], "visible": True},
    {"index": "T4", "label": "T4", "position": {"x": 417.5, "y": 301.0, "z": 0.0}, "size": {"width": 128.0, "height": 86.0, "depth": 0.0}, "content_type": ["96_well_plate"], "visible": True},
    {"index": "T5", "label": "T5", "position": {"x": 5.0, "y": 205.0, "z": 0.0}, "size": {"width": 128.0, "height": 86.0, "depth": 0.0}, "content_type": [], "visible": True},
    {"index": "T6", "label": "T6", "position": {"x": 142.5, "y": 205.0, "z": 0.0}, "size": {"width": 128.0, "height": 86.0, "depth": 0.0}, "content_type": ["trough"], "visible": True},
    {"index": "T7", "label": "T7", "position": {"x": 280.0, "y": 205.0, "z": 0.0}, "size": {"width": 128.0, "height": 86.0, "depth": 0.0}, "content_type": ["trough"], "visible": True},
    {"index": "T8", "label": "T8", "position": {"x": 417.5, "y": 205.0, "z": 0.0}, "size": {"width": 128.0, "height": 86.0, "depth": 0.0}, "content_type": ["trough"], "visible": True},
    {"index": "T9", "label": "T9", "position": {"x": 5.0, "y": 109.0, "z": 0.0}, "size": {"width": 128.0, "height": 86.0, "depth": 0.0}, "content_type": [], "visible": True},
    {"index": "T10", "label": "T10", "position": {"x": 142.5, "y": 109.0, "z": 0.0}, "size": {"width": 128.0, "height": 86.0, "depth": 0.0}, "content_type": ["trough"], "visible": True},
    {"index": "T11", "label": "T11", "position": {"x": 280.0, "y": 109.0, "z": 0.0}, "size": {"width": 128.0, "height": 86.0, "depth": 0.0}, "content_type": ["tip_rack"], "visible": True},
    {"index": "T12", "label": "T12", "position": {"x": 417.5, "y": 109.0, "z": 0.0}, "size": {"width": 128.0, "height": 86.0, "depth": 0.0}, "content_type": ["tip_rack"], "visible": True},
    {"index": "T13", "label": "T13", "position": {"x": 5.0, "y": 13.0, "z": 0.0}, "size": {"width": 128.0, "height": 86.0, "depth": 0.0}, "content_type": [], "visible": True},
    {"index": "T14", "label": "T14", "position": {"x": 142.5, "y": 13.0, "z": 0.0}, "size": {"width": 128.0, "height": 86.0, "depth": 0.0}, "content_type": ["trough"], "visible": True},
    {"index": "T15", "label": "T15", "position": {"x": 280.0, "y": 13.0, "z": 0.0}, "size": {"width": 128.0, "height": 86.0, "depth": 0.0}, "content_type": ["tip_rack"], "visible": True},
    {"index": "T16", "label": "T16", "position": {"x": 417.5, "y": 13.0, "z": 0.0}, "size": {"width": 128.0, "height": 86.0, "depth": 0.0}, "content_type": ["trough"], "visible": True},
]


@resource(
    id="prcxi_deck",
    displayname="PRCXI 台面",
    category=["gn_lab", "deck"],
    description="GN PRCXI 液体处理器 16 槽台面（T1-T16）。T1 废液；T2/T3/T6/T7/T8/T10/T14/T16 储液槽；T4 96 孔酶标板；T11/T12/T15 枪头盒。",
    available_sites=PRCXI_DECK_SITES,
)
class PRCXIDeck(_PRCXI9300Deck):
    """设备包注册：图节点 id 仍为 PRCXI_Deck。"""
