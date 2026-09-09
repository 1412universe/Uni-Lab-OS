"""PRCXI 悬停滴液：legacy Tapping → V04 Dispense 字段透传单元测试。"""

from unilabos.devices.workstation.GN.liquid_handling.prcxi.prcxi import (
    legacy_steps_to_v04_solution_steps,
)


def test_tapping_hover_fields_convert_to_v04_dispense():
  legacy = {
      "Function": "Tapping",
      "StepAxis": "Left",
      "PlateNo": 10,
      "HoleRow": 1,
      "HoleCol": 1,
      "HoleNumbers": "1",
      "DosageNum": 5,
      "BlowVolume": 0,
      "DispensingMethodV04": "BottleNeck",
      "HoverBelowLiquidLevel": 10,
      "ZStartPointOffsetHeight": 8,
      "DosageSpeed": 1,
      "PostDischargePauseTimeMs": 500,
  }
  converted = legacy_steps_to_v04_solution_steps([legacy])
  assert len(converted) == 1
  step = converted[0]
  assert step["Kind"] == "Dispense"
  assert step["DispenseVolume"] == "5.0"
  assert step["DispensingMethod"] == "BottleNeck"
  assert step["HoverBelowLiquidLevel"] == 10
  assert step["ZStartPointOffsetHeight"] == 8
  assert step["DispenseSpeed"] == 1
  assert step["PostDischargePauseTimeMs"] == 500


def test_imbibing_suction_speed_converts_to_v04_aspirate():
  legacy = {
      "Function": "Imbibing",
      "StepAxis": "Left",
      "PlateNo": 6,
      "HoleRow": 1,
      "HoleCol": 1,
      "HoleNumbers": "1",
      "LiquidVolume": 4.8,
      "DosageNum": 4.8,
      "PostAirVolume": 0,
      "HoverBelowLiquidLevel": 5,
      "DosageSpeed": 50,
  }
  converted = legacy_steps_to_v04_solution_steps([legacy])
  assert len(converted) == 1
  step = converted[0]
  assert step["Kind"] == "Aspirate"
  assert step["AspirateVolume"] == "4.8"
  assert step["HoverBelowLiquidLevel"] == 5
  assert step["SuctionSpeed"] == 50


def test_imbibing96_suction_speed_field_preserved():
  """整板吸液路径写入的 DosageSpeed 同样应进入 V04 SuctionSpeed。"""
  legacy = {
      "Function": "Imbibing",
      "StepAxis": "Left",
      "PlateNo": 10,
      "HoleRow": 1,
      "HoleCol": 1,
      "IsWholePlate": True,
      "HoleNumbers": "",
      "LiquidVolume": 390,
      "DosageNum": 390,
      "PostAirVolume": 0,
      "DosageSpeed": 100,
  }
  converted = legacy_steps_to_v04_solution_steps([legacy])
  assert converted[0]["SuctionSpeed"] == 100


def test_tapping_without_hover_keeps_minimal_v04_payload():
  legacy = {
      "Function": "Tapping",
      "StepAxis": "Left",
      "PlateNo": 10,
      "HoleRow": 1,
      "HoleCol": 1,
      "HoleNumbers": "1",
      "DosageNum": 100,
      "BlowVolume": 5,
  }
  converted = legacy_steps_to_v04_solution_steps([legacy])
  step = converted[0]
  assert step["Kind"] == "Dispense"
  assert step["DispenseVolume"] == "100.0"
  assert step["BlowVolume"] == 5.0
  assert "DispensingMethod" not in step
  assert "HoverBelowLiquidLevel" not in step
