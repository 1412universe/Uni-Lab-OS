"""liquid_handler_abstract 悬停滴液参数组装单元测试。"""

from unilabos.devices.workstation.GN.liquid_handling.liquid_handler_abstract import (
    _build_dispense_hover_kwargs,
)


def test_liquid_height_fallback_to_hover_below():
  extras = _build_dispense_hover_kwargs(liquid_height=[5])
  assert extras == {"hover_below_liquid_level": 5}


def test_explicit_hover_overrides_liquid_height_fallback():
  extras = _build_dispense_hover_kwargs(
      liquid_height=[5],
      hover_below_liquid_level=[12],
  )
  assert extras["hover_below_liquid_level"] == 12


def test_full_hover_drip_profile():
  extras = _build_dispense_hover_kwargs(
      dispensing_method=["BottleNeck"],
      hover_below_liquid_level=[10],
      z_start_point_offset_height=[8],
      post_discharge_pause_time_ms=[1000],
      dis_flow_rates=[1],
  )
  assert extras == {
      "dispensing_method": "BottleNeck",
      "hover_below_liquid_level": 10,
      "z_start_point_offset_height": 8,
      "post_discharge_pause_time_ms": 1000,
      "dispense_speed": 1,
  }
