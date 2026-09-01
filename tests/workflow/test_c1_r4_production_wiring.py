"""F06 R4 已发布工作流目录的生产组合与重启恢复 RED。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tests.registry.test_f05_material_source_catalog import _Registry
from unilabos.app.scheduler.inventory.store import InventoryStore
from unilabos.app.workflow_template_api import WorkflowTemplateQueryService
from unilabos.workflow.composition import (
    compose_local_workflow_template_runtime,
    reset_workflow_service_for_test,
)

from .test_c1_r2_static_expansion_contract import (
    CHILD_WORKFLOW_UUID,
    INVOCATION_UUID,
    PARENT_WORKFLOW_UUID,
)

PACKAGE_ID = "c1_product_lab"
CHILD_MODULE = f"{PACKAGE_ID}.workflows.child"


def _child_source() -> str:
    """返回可被启动恢复与静态目录共同消费的空叶工作流源码。

    参数：无。返回：固定身份的空叶工作流 Python 源码。异常：无。
    """

    return f'''from unilabos.workflow.authoring import workflow, workflow_output


@workflow(
    workflow_uuid="{CHILD_WORKFLOW_UUID}",
    displayname="Published child",
    workflow_type="experiment_operation",
)
def prepare_sample():
    return workflow_output()
'''


def _write_package(selected_root: Path) -> None:
    """写入子/父工作流显式授权的可编辑包（Editable Package）。

    参数：``selected_root`` 是测试独占包根。返回：无；在包根写入两项源码和
    一份声明。异常：目录创建或写文件失败时原样抛出 ``OSError``。
    """

    source_path = selected_root / PACKAGE_ID / "workflows" / "child.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(_child_source(), encoding="utf-8")
    parent_path = selected_root / PACKAGE_ID / "workflows" / "parent.py"
    parent_path.write_text(_parent_source(), encoding="utf-8")
    selected_root.joinpath("package.yaml").write_text(
        "package:\n"
        f"  name: {PACKAGE_ID}\n"
        "workflows:\n"
        f"  - workflow_uuid: {CHILD_WORKFLOW_UUID}\n"
        f"    source: {PACKAGE_ID}/workflows/child.py\n"
        f"  - workflow_uuid: {PARENT_WORKFLOW_UUID}\n"
        f"    source: {PACKAGE_ID}/workflows/parent.py\n",
        encoding="utf-8",
    )


def _parent_source() -> str:
    """返回只调用发布叶工作流、不创建任务或物理动作的父作者源码。

    参数：无。返回：带稳定调用节点身份的父工作流 Python 源码。异常：无。
    """

    return f'''from {CHILD_MODULE} import prepare_sample
from unilabos.workflow.authoring import workflow, workflow_output


@workflow(
    workflow_uuid="{PARENT_WORKFLOW_UUID}",
    displayname="Product parent",
)
def product_parent():
    # unilab:node_uuid={INVOCATION_UUID}
    result = prepare_sample()
    return workflow_output()
'''


def _empty_parent_graph() -> dict[str, object]:
    """构造生产编译器首次调用使用的空父工作流图。

    参数：无。返回：修订为一的空父图。异常：无。
    """

    return {
        "workflow": {
            "uuid": PARENT_WORKFLOW_UUID,
            "revision": 1,
            "name": "Product parent",
            "tags": [],
            "description": None,
            "meta_data": {},
        },
        "nodes": [],
        "edges": [],
        "node_templates": [],
        "handle_templates": [],
    }


def _contract_extension(template: object) -> dict[str, object]:
    """从目录模板对象或持久 JSON 文本读取发布工作流扩展。

    参数：``template`` 是目录模板字典，其中 Schema 可为字典或 JSON 文本。
    返回：发布工作流合同扩展。异常：形状错误时由断言或 JSON 解析抛出。
    """

    assert isinstance(template, dict)
    schema = template["schema"]
    if isinstance(schema, str):
        schema = json.loads(schema)
    assert isinstance(schema, dict)
    extension = schema["x-unilabos-workflow-contract"]
    assert isinstance(extension, dict)
    return extension


def test_product_composition_publishes_and_rebuilds_workflow_templates(
    tmp_path: Path,
) -> None:
    """生产组合发布同代工作流模板，并在重启后从源码重建相同身份。

    参数：``tmp_path`` 隔离产品数据库与包根。返回：无；断言发布、应用重建与
    重启重建。异常：产品组合或断言失败时由 pytest 报告。
    """

    reset_workflow_service_for_test()
    selected_root = tmp_path / "editable"
    selected_root.mkdir()
    _write_package(selected_root)
    inventory_store = InventoryStore(str(tmp_path / "inventory.db"))
    try:
        service, projection = compose_local_workflow_template_runtime(
            tmp_path,
            inventory_store=inventory_store,
            registry=_Registry(),
            editable_package_roots=(selected_root,),
        )
        action = projection.snapshot().require_action(
            f"{CHILD_MODULE}:prepare_sample",
            f"workflow:{CHILD_WORKFLOW_UUID}",
        )
        first_template_uuid = str(action.template["uuid"])
        assert action.template["type"] == "workflow"
        assert service.compiler is not None
        compiled = service.compiler.compile(
            workflow_uuid=PARENT_WORKFLOW_UUID,
            workflow_revision=1,
            python_source=_parent_source(),
            source_uri=f"package://{PACKAGE_ID}/workflows/parent.py",
            applied_graph=_empty_parent_graph(),
        )
        assert compiled.valid and compiled.graph is not None, compiled.diagnostics
        query = WorkflowTemplateQueryService(projection)
        page = query.list_node_templates(
            page=1,
            page_size=20,
            keyword="",
            resource_template_uuid=None,
            action_type="",
            node_type="workflow",
        )
        assert [item["uuid"] for item in page["items"]] == [first_template_uuid]

        authoring = service.get_authoring(CHILD_WORKFLOW_UUID)
        assert authoring["state"] == "applied"
        assert authoring["candidate"] is None
        assert authoring["draft"]["diagnostics"] == []
        refreshed = projection.snapshot().require_action(
            f"{CHILD_MODULE}:prepare_sample",
            f"workflow:{CHILD_WORKFLOW_UUID}",
        )
        extension = _contract_extension(refreshed.detached_template())
        assert extension["workflow_revision"] == service.get_graph(
            CHILD_WORKFLOW_UUID
        )["workflow"]["revision"]
        assert service.compiler is not None
        assert service.compiler.template_catalog_fingerprint == (
            projection.snapshot().fingerprint
        )
        with sqlite3.connect(tmp_path / "workflow_history.db") as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM workflow_node_template"
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT COUNT(*) FROM workflow_handle_template"
            ).fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM workflow").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM workflow_node").fetchone()[0] == 0

        reset_workflow_service_for_test()
        restarted, restarted_projection = compose_local_workflow_template_runtime(
            tmp_path,
            inventory_store=inventory_store,
            registry=_Registry(),
            editable_package_roots=(selected_root,),
        )
        restored = restarted_projection.snapshot().require_action(
            f"{CHILD_MODULE}:prepare_sample",
            f"workflow:{CHILD_WORKFLOW_UUID}",
        )
        assert restored.template["uuid"] == first_template_uuid
        assert restarted.compiler is not None
    finally:
        reset_workflow_service_for_test()
        inventory_store.close()


def test_composite_task_persists_only_parent_workflow_authority(
    tmp_path: Path,
) -> None:
    """组合调用只建立父工作流任务（WorkflowTask）写权威。

    参数：``tmp_path`` 隔离源码包、工作流数据库与库存数据库。返回：无；断言
    应用含组合调用的父图后只持久化一个归属父工作流的任务，不为子工作流另建
    任务。异常：产品组合、应用、计划或持久化失败时由 pytest 直接报告。
    """

    reset_workflow_service_for_test()
    selected_root = tmp_path / "editable"
    selected_root.mkdir()
    _write_package(selected_root)
    inventory_store = InventoryStore(str(tmp_path / "inventory.db"))
    try:
        service, _projection = compose_local_workflow_template_runtime(
            tmp_path,
            inventory_store=inventory_store,
            registry=_Registry(),
            editable_package_roots=(selected_root,),
        )
        parent_authoring = service.get_authoring(PARENT_WORKFLOW_UUID)
        assert parent_authoring["state"] == "applied"
        assert parent_authoring["candidate"] is None
        assert parent_authoring["draft"]["diagnostics"] == []
        parent_graph = service.get_graph(PARENT_WORKFLOW_UUID)
        assert {node["uuid"] for node in parent_graph["nodes"]} == {
            INVOCATION_UUID
        }

        task = service.create_workflow_task(
            workflow_uuid=PARENT_WORKFLOW_UUID,
            run_mode="normal",
            target_node_uuid=None,
            input_value={},
            description=None,
            meta_data={},
        )

        page = service.list_workflow_tasks(page=1, page_size=20)
        assert page["total"] == 1
        assert [item["uuid"] for item in page["items"]] == [task["uuid"]]
        assert page["items"][0]["workflow_uuid"] == PARENT_WORKFLOW_UUID
        assert page["items"][0]["workflow_uuid"] != CHILD_WORKFLOW_UUID
        assert service.list_workflow_node_jobs(task["uuid"]) == []
    finally:
        reset_workflow_service_for_test()
        inventory_store.close()
