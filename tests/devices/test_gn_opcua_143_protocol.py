"""OPC UA 1.4.3 协议升级回归：点位表、指令表与驱动映射。

对应协议文件：``OPC_UA协议1.4.3.xlsx``（点位表 + 锁紧预锁说明）。
节点表由该 xlsx 生成，落在 ``unilabos/devices/workstation/GN/opcua_gn1.4.3.csv``。
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

GN_DIR = Path(__file__).resolve().parents[2] / "unilabos" / "devices" / "workstation" / "GN"
CSV_143 = GN_DIR / "opcua_gn1.4.3.csv"
CSV_139 = GN_DIR / "opcua_gn1.3.9.csv"

# 1.4.3 相对 1.3.9 的增删（点位表实际 diff）
ADDED = {
    "Lock_PreTurnsSet": "INT16",
    "Tare_the_scale": "INT16",
    "scale_weight": "FLOAT",  # 协议写 REAL，OPC 侧按 FLOAT 读写
    "Centrifuge_Time_Hour": "INT16",
}
REMOVED = {
    "Centrifuge_Temperature_feedback",
    "Solid_Remove_static",
}


def _read(path: Path) -> dict[str, dict[str, str]]:
    """按 Name 索引节点表；`path` 必须是 1.4.3/1.3.9 同格式 CSV。

    历史节点表（1.3.9 及更早）是 GBK 编码，与 `BaseOpcUaClient.load_csv` 保持
    同样的回退顺序，否则读基线表会直接 UnicodeDecodeError。
    """

    table: dict[str, dict[str, str]] = {}
    last_error: UnicodeDecodeError | None = None
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            with path.open(newline="", encoding=encoding) as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    name = (row.get("Name") or "").strip()
                    node_id = (row.get("NodeId") or "").strip()
                    if name and node_id.startswith("ns="):
                        table[name] = row
            return table
        except UnicodeDecodeError as error:
            last_error = error
            table = {}
    raise last_error or UnicodeDecodeError("utf-8", b"", 0, 1, "unable to decode CSV")


@pytest.fixture(scope="module")
def table_143() -> dict[str, dict[str, str]]:
    """1.4.3 节点表。"""

    return _read(CSV_143)


@pytest.fixture(scope="module")
def table_139() -> dict[str, dict[str, str]]:
    """1.3.9 节点表（升级前的基线）。"""

    return _read(CSV_139)


def test_protocol_file_exists() -> None:
    """1.4.3 节点表必须随包提交，否则驱动启动时加载不到任何节点。"""

    assert CSV_143.is_file()
    table = _read(CSV_143)
    assert len(table) >= 230


def test_new_points_are_present(table_143: dict[str, dict[str, str]]) -> None:
    """1.4.3 新增的四个点位必须存在且数据类型正确。"""

    for name, expected in ADDED.items():
        assert name in table_143, f"{name} 缺失"
        assert (table_143[name]["DataType"] or "").upper() == expected


def test_removed_points_are_gone(table_143: dict[str, dict[str, str]]) -> None:
    """协议已删除的点位不能留在节点表里。

    留着会让 `use_node()` 拿到一个 PLC 上不存在的 NodeId，读写时报 BadNodeIdUnknown
    而不是清晰地「没有这个点位」。
    """

    for name in REMOVED:
        assert name not in table_143, f"{name} 应已从 1.4.3 移除"


def test_node_ids_are_unchanged_for_shared_points(
    table_143: dict[str, dict[str, str]],
    table_139: dict[str, dict[str, str]],
) -> None:
    """共享点位的 NodeId 必须一字不差，否则整站读写会全错位。"""

    shared = set(table_143) & set(table_139)
    assert len(shared) > 200
    mismatched = [
        name for name in shared
        if table_143[name]["NodeId"].strip() != table_139[name]["NodeId"].strip()
    ]
    assert mismatched == []


def test_command_tables_carry_143_change(table_143: dict[str, dict[str, str]]) -> None:
    """指令表要体现 1.4.3 的两处语义变化。"""

    lock_cmd = table_143["Lock_CmdType"]["设备模块指令"]
    # 11 由「电批拧紧」改为「终锁」，并新增 15=预锁
    assert "11=电批终锁" in lock_cmd
    assert "15=电批预锁" in lock_cmd

    centrifuge_cmd = table_143["Centrifuge_CmdType"]["设备模块指令"]
    # 离心机新增 11=配重天平去皮
    assert "11=配重天平去皮" in centrifuge_cmd

    tube_cmd = table_143["Tube_CmdType"]["设备模块指令"]
    # 1.3.9 里 48/49 挤在 47 后面没有编号；1.4.3 明确了 48、49
    assert "48" in tube_cmd and "49" in tube_cmd


def test_locking_driver_exposes_pre_tighten() -> None:
    """锁紧驱动要能下发预锁圈数，并在缺参时关闭式失败。"""

    from unilabos.devices.workstation.GN.gn_locking_mechanism import (
        LOCK_CMD_LABELS,
        LockCommand,
        LockingMechanismDevice,
    )

    assert int(LockCommand.SCREW_PRE_TIGHTEN) == 15
    assert int(LockCommand.SCREW_FINAL_TIGHTEN) == 11
    # 旧名作为枚举别名仍可用，且指向同一个成员
    assert LockCommand.SCREW_TIGHTEN is LockCommand.SCREW_FINAL_TIGHTEN
    assert "终锁" in LOCK_CMD_LABELS[11]
    assert "预锁" in LOCK_CMD_LABELS[15]

    device = LockingMechanismDevice.__new__(LockingMechanismDevice)
    setpoints = device._build_setpoints(x_pos=1270, y_pos=2030, pre_turns=20)
    assert setpoints["Lock_PreTurnsSet"] == 20
    # 未传 pre_turns 时不得凭空写 0（协议要求 ≤0 拒收）
    assert "Lock_PreTurnsSet" not in device._build_setpoints(x_pos=1)

    device._run = lambda *a, **k: {"success": True}  # 不触达 OPC
    with pytest.raises(ValueError, match="pre_turns"):
        device.execute_command(cmd_type=15)
    with pytest.raises(ValueError, match="pre_turns"):
        device.execute_command(cmd_type=15, pre_turns=0)


def test_centrifuge_driver_exposes_tare_and_hour() -> None:
    """离心机驱动要覆盖 1.4.3 的 11=去皮 与小时级时长。"""

    from unilabos.devices.workstation.GN.gn_centrifuge import (
        CENTRIFUGE_CMD_LABELS,
        CentrifugeCommand,
        CentrifugeDevice,
    )

    assert int(CentrifugeCommand.TARE_SCALE) == 11
    assert CENTRIFUGE_CMD_LABELS[11] == "配重天平去皮"
    assert CentrifugeDevice.TIME_HOUR_NODE == "Centrifuge_Time_Hour"
    assert CentrifugeDevice.SCALE_WEIGHT_NODE == "scale_weight"

    device = CentrifugeDevice.__new__(CentrifugeDevice)
    setpoints = device._build_setpoints(
        cmd_type=int(CentrifugeCommand.RUN),
        rpm=1000,
        time_minutes=30,
        time_hours=2,
        plate_no=2,
    )
    assert setpoints["Centrifuge_Time"] == 30
    assert setpoints["Centrifuge_Time_Hour"] == 2
    # 去皮没有任何设定值，只靠 CmdType + CompleteFB 握手
    assert device._build_setpoints(cmd_type=int(CentrifugeCommand.TARE_SCALE)) == {}
    # 去皮不能走「Y/Z 反馈稳定」兜底：它不产生任何轴运动
    assert int(CentrifugeCommand.TARE_SCALE) not in CentrifugeDevice._INTERNAL_CMDS


def test_all_drivers_point_at_143() -> None:
    """所有 GN 驱动都必须指向同一份 1.4.3 节点表。"""

    offenders: dict[str, list[str]] = {}
    for path in sorted(GN_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        stale = [
            token for token in ("opcua_gn1.3.6.csv", "opcua_gn1.3.7.csv",
                                "opcua_gn1.3.9.csv")
            if f'"{token}"' in text
        ]
        if stale:
            offenders[path.name] = stale
    assert offenders == {}
