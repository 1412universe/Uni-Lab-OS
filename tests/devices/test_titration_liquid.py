"""titration_liquid 96 孔滴定路径单元测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("pylabrobot")
from pylabrobot.resources import Trash

from unilabos.devices.workstation.GN.liquid_handling.liquid_handler_abstract import (
    LiquidHandlerAbstract,
)


class _TitrationTestHandler(LiquidHandlerAbstract):
    """最小化 titration 测试 handler：记录 *96 调用。"""

    def __init__(self, deck, *, has_trash: bool = False):
        backend = MagicMock()
        super().__init__(backend, deck, simulator=False, channel_num=8)
        self._calls: List[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._has_trash = has_trash

    async def _resolve_to_plr_resources(self, resources):  # type: ignore[override]
        return list(resources)

    def set_tiprack(self, tip_racks):  # type: ignore[override]
        pass

    async def pick_up_tips96(self, tip_rack, offset=None, **kwargs):
        self._calls.append(("pick_up_tips96", (tip_rack,), {"offset": offset, **kwargs}))

    async def aspirate96(self, resource, volume, **kwargs):
        self._calls.append(("aspirate96", (resource, volume), kwargs))

    async def dispense96(self, resource, volume, **kwargs):
        self._calls.append(("dispense96", (resource, volume), kwargs))

    async def drop_tips96(self, resource, **kwargs):
        self._calls.append(("drop_tips96", (resource,), kwargs))

    async def custom_delay(self, seconds=0, msg=None):
        self._calls.append(("custom_delay", (seconds,), {"msg": msg}))

    async def pause_step_action(
        self,
        pause_enum: str = "Timing",
        pause_time: Optional[str] = "60",
        remarks: str = "",
    ):
        self._calls.append(
            (
                "pause_step_action",
                (),
                {"pause_enum": pause_enum, "pause_time": pause_time, "remarks": remarks},
            )
        )

    def _reflect_96well_transfer(self, source_plate, target_plate, volume):  # type: ignore[override]
        self._calls.append(
            ("_reflect_96well_transfer", (source_plate, target_plate, volume), {})
        )


def _make_plate(name: str):
    return SimpleNamespace(name=name, parent=None, children=[])


def _make_deck(*, with_trash: bool = False):
    children: list[Any] = []
    if with_trash:
        children.append(Trash(name="trash", size_x=50, size_y=50, size_z=10))
    return SimpleNamespace(name="deck", children=children)


@pytest.mark.asyncio
async def test_titration_repeat_count_calls_pick_asp_disp_drop_once():
    source = _make_plate("source_plate")
    target = _make_plate("target_plate")
    tip_rack = SimpleNamespace(name="tips", parent=None)
    deck = _make_deck()
    handler = _TitrationTestHandler(deck)

    with patch.object(handler, "deck", deck):
        result = await handler.titration_liquid(
            [source],
            [target],
            [tip_rack],
            asp_vols=[10.0],
            dis_vols=[10.0],
            repeat_count=3,
            is_96_well=True,
        )

    assert isinstance(result, dict)
    pick_calls = [c for c in handler._calls if c[0] == "pick_up_tips96"]
    drop_calls = [c for c in handler._calls if c[0] == "drop_tips96"]
    asp_calls = [c for c in handler._calls if c[0] == "aspirate96"]
    disp_calls = [c for c in handler._calls if c[0] == "dispense96"]
    assert len(pick_calls) == 1
    assert len(drop_calls) == 1
    assert len(asp_calls) == 3
    assert len(disp_calls) == 3


@pytest.mark.asyncio
async def test_titration_default_dispensing_method_bottle_neck():
    source = _make_plate("source_plate")
    target = _make_plate("target_plate")
    tip_rack = SimpleNamespace(name="tips", parent=None)
    handler = _TitrationTestHandler(_make_deck())

    await handler.titration_liquid(
        [source],
        [target],
        [tip_rack],
        asp_vols=[5.0],
        dis_vols=[5.0],
        repeat_count=1,
        is_96_well=True,
    )

    disp_calls = [c for c in handler._calls if c[0] == "dispense96"]
    assert len(disp_calls) == 1
    assert disp_calls[0][2].get("dispensing_method") == "BottleNeck"


@pytest.mark.asyncio
async def test_titration_cycle_delay_between_loops():
    source = _make_plate("source_plate")
    target = _make_plate("target_plate")
    tip_rack = SimpleNamespace(name="tips", parent=None)
    handler = _TitrationTestHandler(_make_deck())

    await handler.titration_liquid(
        [source],
        [target],
        [tip_rack],
        asp_vols=[1.0],
        dis_vols=[1.0],
        repeat_count=3,
        cycle_delay_s=180,
        is_96_well=True,
    )

    pause_calls = [c for c in handler._calls if c[0] == "pause_step_action"]
    assert len(pause_calls) == 2
    assert pause_calls[0][2]["pause_enum"] == "Timing"
    assert pause_calls[0][2]["pause_time"] == "180"


@pytest.mark.asyncio
async def test_titration_drop_target_trash_or_tip_rack():
    source = _make_plate("source_plate")
    target = _make_plate("target_plate")
    tip_rack = SimpleNamespace(name="tips", parent=None)

    handler_no_trash = _TitrationTestHandler(_make_deck(with_trash=False))
    await handler_no_trash.titration_liquid(
        [source], [target], [tip_rack], asp_vols=[1.0], dis_vols=[1.0], is_96_well=True
    )
    drop_no_trash = [c for c in handler_no_trash._calls if c[0] == "drop_tips96"][0]
    assert drop_no_trash[1][0] is tip_rack

    handler_trash = _TitrationTestHandler(_make_deck(with_trash=True))
    with patch.object(handler_trash, "deck", _make_deck(with_trash=True)):
        await handler_trash.titration_liquid(
            [source], [target], [tip_rack], asp_vols=[1.0], dis_vols=[1.0], is_96_well=True
        )
    drop_trash = [c for c in handler_trash._calls if c[0] == "drop_tips96"][0]
    assert isinstance(drop_trash[1][0], Trash)


@pytest.mark.asyncio
async def test_titration_blow_air_before_and_post_blow_out():
    source = _make_plate("source_plate")
    target = _make_plate("target_plate")
    tip_rack = SimpleNamespace(name="tips", parent=None)
    handler = _TitrationTestHandler(_make_deck())

    await handler.titration_liquid(
        [source],
        [target],
        [tip_rack],
        asp_vols=[4.8],
        dis_vols=[4.8],
        blow_out_air_volume_before=[300],
        post_air_volume=[0],
        blow_out_air_volume=[5],
        repeat_count=2,
        is_96_well=True,
    )

    asp_calls = [c for c in handler._calls if c[0] == "aspirate96"]
    assert len(asp_calls) == 4  # 每轮：预吸空气 + 正式吸液
    pre_air_calls = [c for c in asp_calls if c[1][1] == 0.0]
    assert len(pre_air_calls) == 2
    assert pre_air_calls[0][2].get("blow_out_air_volume") == 300

    main_asp = [c for c in asp_calls if c[1][1] == 4.8]
    assert len(main_asp) == 2
    assert main_asp[0][2].get("post_air_volume") == 0

    disp_calls = [c for c in handler._calls if c[0] == "dispense96"]
    assert len(disp_calls) == 2
    assert disp_calls[0][2].get("blow_out_air_volume") == 5


def test_cycle_param_scalar_and_list():
    assert LiquidHandlerAbstract._cycle_param(None, 0) is None
    assert LiquidHandlerAbstract._cycle_param(7, 3) == 7
    assert LiquidHandlerAbstract._cycle_param([10, 20], 0) == 10
    assert LiquidHandlerAbstract._cycle_param([10, 20], 3) == 20
