"""ChemWorldModel / 旧版 JSON 导入重映射测试。"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unilabos.workflow.authoring_engine import WorkflowAuthoringEngine
from unilabos.workflow.authoring_kernel import AuthoringCatalogSnapshot
from unilabos.workflow.legacy_json_import import (
    empty_as_none,
    normalize_imported_action_name,
    normalize_imported_edge_uuid,
    normalize_imported_node_uuid,
    resolve_catalog_action,
    resolve_handle_uuid,
)
from tests.workflow.test_domain_workflow_source_sync import (
    _empty_domain_package,
    _project_compiled_catalog,
    _service,
)

LOCAL_TEMPLATE_UUID = "e82ebb80-df36-5ec0-82a6-8e4720be3c10"
FOREIGN_TEMPLATE_UUID = "8cfeb6d6-1121-49e4-9848-100e37782793"
READY_SOURCE_UUID = "101036fa-0000-4000-8000-000000000001"
READY_TARGET_UUID = "19701e83-0000-4000-8000-000000000002"
NODE_A_UUID = "4aea3978-d1b4-431a-bfc1-3cc2851e25d8"
NODE_B_UUID = "00ee4f6d-fcd6-4853-94e6-c01b78071279"


def _cwm_style_template() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """构造带 community 设备名的本地动作模板，模拟 GN 离心管目录项。"""

    template = {
        "uuid": LOCAL_TEMPLATE_UUID,
        "resource_template_uuid": "3f8a794b-7d03-5e79-b15b-0fda26ae0e4b",
        "name": "execute_command",
        "display_name": "execute_command",
        "class": (
            "gn_lab.devices.gn_station_devices:"
            "CentrifugeTubeLiquidHandlingDevice"
        ),
        "description": "离心管命令",
        "meta_data": {
            "unilab": {
                "resource_template": {
                    "name": "community.gn_lab.gn_centrifuge_tube_liquid_handling",
                }
            }
        },
        "goal": {},
        "goal_default": {},
        "feedback": {},
        "result": {},
        "schema": None,
        "type": "UniLabJsonCommand",
        "node_type": "ILab",
        "icon": None,
        "header": None,
        "footer": None,
    }
    handles = [
        {
            "uuid": READY_TARGET_UUID,
            "workflow_node_template_uuid": LOCAL_TEMPLATE_UUID,
            "handle_key": "ready",
            "io_type": "target",
            "display_name": "Ready",
            "type": "any",
            "required": False,
            "data_source": "dependency",
            "data_key": "ready",
            "description": None,
            "meta_data": {},
        },
        {
            "uuid": READY_SOURCE_UUID,
            "workflow_node_template_uuid": LOCAL_TEMPLATE_UUID,
            "handle_key": "ready",
            "io_type": "source",
            "display_name": "Ready",
            "type": "any",
            "required": False,
            "data_source": "dependency",
            "data_key": "ready",
            "description": None,
            "meta_data": {},
        },
    ]
    return template, handles


def test_normalize_strips_auto_prefix() -> None:
    assert normalize_imported_action_name("auto-execute_command") == "execute_command"
    assert normalize_imported_action_name("execute_command") == "execute_command"
    assert empty_as_none("") is None
    assert empty_as_none("  ") is None
    assert empty_as_none(None) is None
    assert empty_as_none(FOREIGN_TEMPLATE_UUID) == FOREIGN_TEMPLATE_UUID


def test_resolve_catalog_action_by_device_and_action() -> None:
    template, handles = _cwm_style_template()
    snapshot = AuthoringCatalogSnapshot.from_entities([template], handles)
    matched = resolve_catalog_action(
        snapshot.actions,
        {
            "device_name": "gn_centrifuge_tube_liquid_handling",
            "resource_name": "gn_centrifuge_tube_liquid_handling",
            "template_name": "auto-execute_command",
            "template_uuid": FOREIGN_TEMPLATE_UUID,
        },
    )
    assert matched is not None
    assert str(matched.template["uuid"]) == LOCAL_TEMPLATE_UUID
    assert resolve_handle_uuid(matched, handle_key="ready", io_type="source") == (
        READY_SOURCE_UUID
    )
    assert resolve_handle_uuid(matched, handle_key="ready", io_type="target") == (
        READY_TARGET_UUID
    )


def test_import_chemworldmodel_json_remaps_template_and_handles(
    tmp_path: Path,
) -> None:
    """CWM 导出的外源 template_uuid + ready 键应自动落到本机目录。"""

    template, handles = _cwm_style_template()
    engine = WorkflowAuthoringEngine(
        catalog=AuthoringCatalogSnapshot.from_entities([template], handles)
    )
    selected_root = tmp_path / "domain"
    _empty_domain_package(selected_root)
    service, _runtime_store, definitions = _service(
        database_path=tmp_path / "workflow_history.db",
        selected_root=selected_root,
        engine=engine,
    )

    class _CatalogProjection:
        graph = {"node_templates": [template], "handle_templates": handles}
        template_catalog_fingerprint = engine.template_catalog_fingerprint

    _project_compiled_catalog(definitions, _CatalogProjection)
    try:
        imported = service.import_legacy_workflow(
            payload={
                "name": "CWM 离心管单点",
                "data": {
                    "name": "CWM 离心管单点",
                    "nodes": [
                        {
                            "uuid": NODE_A_UUID,
                            "name": "auto-execute_command",
                            "template_name": "auto-execute_command",
                            "template_uuid": FOREIGN_TEMPLATE_UUID,
                            "device_name": "gn_centrifuge_tube_liquid_handling",
                            "resource_name": "gn_centrifuge_tube_liquid_handling",
                            "parent_uuid": "",
                            "type": "UniLabJsonCommand",
                            "param": {"command": "noop"},
                            "pose": {},
                            "disabled": False,
                            "minimized": False,
                        },
                        {
                            "uuid": NODE_B_UUID,
                            "name": "auto-execute_command",
                            "template_name": "auto-execute_command",
                            "template_uuid": FOREIGN_TEMPLATE_UUID,
                            "device_name": "gn_centrifuge_tube_liquid_handling",
                            "resource_name": "gn_centrifuge_tube_liquid_handling",
                            "parent_uuid": "",
                            "type": "UniLabJsonCommand",
                            "param": {"command": "noop2"},
                            "pose": {},
                            "disabled": False,
                            "minimized": False,
                        },
                    ],
                    "edges": [
                        {
                            "source_node_uuid": NODE_A_UUID,
                            "target_node_uuid": NODE_B_UUID,
                            "source_handle_key": "ready",
                            "target_handle_key": "ready",
                            "source_handle_io": "source",
                            "target_handle_io": "target",
                        }
                    ],
                },
            }
        )
        assert len(imported["nodes"]) == 2
        assert all(
            node["workflow_node_template_uuid"] == LOCAL_TEMPLATE_UUID
            for node in imported["nodes"]
        )
        assert all(node.get("parent_uuid") in (None, "") for node in imported["nodes"])
        assert len(imported["edges"]) == 1
        edge = imported["edges"][0]
        assert edge["source_handle_uuid"] == READY_SOURCE_UUID
        assert edge["target_handle_uuid"] == READY_TARGET_UUID
        assert edge["source_node_uuid"] != NODE_A_UUID
        assert edge["target_node_uuid"] != NODE_B_UUID

        # 同名再导一次应自动加时间后缀，而不是报函数名冲突。
        second = service.import_legacy_workflow(
            payload={
                "name": "CWM 离心管单点",
                "data": {
                    "name": "CWM 离心管单点",
                    "nodes": [
                        {
                            "uuid": NODE_A_UUID,
                            "name": "auto-execute_command",
                            "template_name": "auto-execute_command",
                            "template_uuid": FOREIGN_TEMPLATE_UUID,
                            "device_name": "gn_centrifuge_tube_liquid_handling",
                            "resource_name": "gn_centrifuge_tube_liquid_handling",
                            "parent_uuid": "",
                            "type": "UniLabJsonCommand",
                            "param": {"command": "noop"},
                            "pose": {},
                            "disabled": False,
                            "minimized": False,
                        }
                    ],
                    "edges": [],
                },
            }
        )
        assert second["workflow"]["name"].startswith("CWM 离心管单点_")
        assert second["workflow"]["name"] != imported["workflow"]["name"]
        assert second["workflow"]["uuid"] != imported["workflow"]["uuid"]
    finally:
        service.close()


def test_compound_cwm_node_uuid_falls_back_to_last_segment() -> None:
    """CWM 组合工作流把祖先路径拼进节点身份，导入时取末段作为节点 UUID。

    形如 ``<根>__<子流程>__<节点>`` 的三段串共 112 个字符，直接当成 UUID 校验
    必然失败；末段是该节点自身 UUID，且同一导出内唯一，取它既稳定又能让
    ``edges`` 端点一一对上。
    """

    root = "30884c7e-c13e-48b7-9560-a97992f78c95"
    child = "cb54a6be-9014-4225-8b4d-f237936edcde"
    node = "6b9be2ee-7240-4e34-bd48-ffc64bd7f89c"

    assert normalize_imported_node_uuid(f"{root}__{child}__{node}") == node
    # 本来就是标准 UUID 的原样返回（大小写归一）
    assert normalize_imported_node_uuid(node) == node
    assert normalize_imported_node_uuid(node.upper()) == node


def test_compound_cwm_node_uuid_rejects_unparseable_values() -> None:
    """末段不是合法 UUID 时必须失败关闭，不能静默编造身份。"""

    for bad in (None, "", "not-a-uuid", "a__b__c"):
        try:
            normalize_imported_node_uuid(bad)
        except ValueError:
            continue
        raise AssertionError(f"应当拒绝：{bad!r}")


def test_composite_edge_label_has_no_standalone_uuid() -> None:
    """CWM 的 ``<源>><目标>`` 边标识不含独立 UUID，交给调用方重新生成。"""

    assert normalize_imported_edge_uuid(
        "30884c7e-c13e-48b7-9560-a97992f78c95__cb54a6be-9014-4225-8b4d-f237936edcde"
        "__5470da9e-f659-4b98-a9a8-a1b72429b7c2>64932934-d435-4d54-b68a-7e65f50c32be"
    ) is None
