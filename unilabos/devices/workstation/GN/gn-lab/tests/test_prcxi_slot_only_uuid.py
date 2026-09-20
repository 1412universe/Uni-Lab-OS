from __future__ import annotations

from unilabos.devices.workstation.GN.liquid_handling.prcxi.prcxi import PRCXI9300Handler


def _handler() -> PRCXI9300Handler:
    handler = PRCXI9300Handler.__new__(PRCXI9300Handler)
    handler.deck = None
    handler._ros_node = None
    handler.slot_only_mode = True
    handler._slot_only_registry = {}
    handler._slot_only_ephemeral_cache = {}
    handler._uuid_name_cache = {}
    handler._uuid_slot_cache = {}
    handler._backend_material_cache = {}
    return handler


def test_uuid_only_mapping_uses_backend_name_and_site() -> None:
    handler = _handler()
    handler._lookup_backend_material = lambda uid: {  # type: ignore[method-assign]
        "uuid": uid,
        "name": "PRCXI_96_DeepWell5",
        "current_site": {"name": "T16"},
    }
    orig = {"uuid": "e74c153c-a29f-4653-a665-b9e1cc1711a9"}
    assert handler._resource_name_from_mapping(orig) == "PRCXI_96_DeepWell5"
    enriched = handler._enrich_slot_only_mapping(orig)
    assert enriched["name"] == "PRCXI_96_DeepWell5"
    assert enriched["slot"] == 16


def test_instantiate_uuid_only_resource() -> None:
    handler = _handler()
    handler._lookup_backend_material = lambda uid: {  # type: ignore[method-assign]
        "uuid": uid,
        "name": "PRCXI_nest_1_troughplate4",
        "current_site": {"name": "T7"},
    }
    res = handler._instantiate_slot_only_plr_resource(
        {"uuid": "bd6f7d88-e96a-4efa-b8c6-f5514aeb7cdf"}
    )
    assert res.name == "PRCXI_nest_1_troughplate4"
    extra = getattr(res, "unilabos_extra", {})
    assert extra.get("uuid") == "bd6f7d88-e96a-4efa-b8c6-f5514aeb7cdf"
    assert extra.get("update_resource_site") == "T7"
    assert handler._lookup_slot_only_position(res) == 7


def test_uuid_only_without_backend_still_errors() -> None:
    handler = _handler()
    try:
        handler._instantiate_slot_only_plr_resource({"uuid": "00000000-0000-4000-8000-000000000001"})
    except ValueError as exc:
        assert "uuid" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_name_still_preferred_over_uuid() -> None:
    handler = _handler()
    handler._lookup_backend_material = lambda uid: {"name": "wrong"}  # type: ignore[method-assign]
    name = handler._resource_name_from_mapping(
        {"name": "PRCXI_1250uL_Tips2", "uuid": "695548f2-3e97-4913-a2c5-e19b3b07cac9"}
    )
    assert name == "PRCXI_1250uL_Tips2"


def test_used_tips_drop_to_t1_trash() -> None:
    from unilabos.devices.workstation.GN.liquid_handling.prcxi.prcxi import PRCXI9300Deck

    handler = _handler()
    handler.deck = PRCXI9300Deck(name="PRCXI_Deck", size_x=825, size_y=400, size_z=17)
    handler.deck._sites[0]["content_type"] = ["trash"]
    handler._unilabos_backend = type("B", (), {"is_9320": True})()
    trash = handler._ensure_trash_resource()
    assert trash.name == "trash"
    assert trash.unilabos_extra.get("update_resource_site") == "T1"
    assert handler._resolve_96_drop_target(object()) is trash  # type: ignore[arg-type]
    assert handler._default_trash_slot_number(handler.deck) == 1


def test_attach_without_site_does_not_park_first_empty_slot() -> None:
    from unilabos.devices.workstation.GN.liquid_handling.prcxi.prcxi import PRCXI9300Deck
    from unilabos.devices.workstation.GN.liquid_handling.prcxi.prcxi_labware import PRCXI_96_DeepWell

    handler = _handler()
    handler.deck = PRCXI9300Deck(name="PRCXI_Deck", size_x=825, size_y=400, size_z=17)
    handler.slot_only_mode = True
    plate = PRCXI_96_DeepWell("orphan")
    plate.unilabos_extra = {}
    out = handler._attach_resources_to_deck_if_needed([plate])
    assert out[0] is plate
    assert plate.parent is None
    assert plate not in list(handler.deck.children or [])
