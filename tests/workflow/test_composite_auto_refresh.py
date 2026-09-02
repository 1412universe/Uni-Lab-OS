"""实验操作应用后自动刷新引用方工作流的公开行为合同。"""

from __future__ import annotations

from pathlib import Path

from tests.registry.test_f05_material_source_catalog import _Registry
from unilabos.app.scheduler.inventory.store import InventoryStore
from unilabos.workflow.composition import (
    compose_local_workflow_template_runtime,
    reset_workflow_service_for_test,
)
from unilabos.workflow.service import WorkflowService

from .test_c1_r2_static_expansion_contract import (
    CHILD_WORKFLOW_UUID,
    INVOCATION_UUID,
    PARENT_WORKFLOW_UUID,
)
from .test_c1_r4_production_wiring import _child_source, _write_package


def _apply_source(
    service: WorkflowService,
    workflow_uuid: str,
    python_source: str,
) -> dict[str, object]:
    """保存并应用一份领域包工作流源码。

    参数：``service`` 是真实 Local 工作流服务，``workflow_uuid`` 是待修改定义，
    ``python_source`` 是完整作者源码。返回：公共 Apply 结果。异常：草稿、目录或
    候选冲突由公共服务原样抛出；测试不绕过源码 CAS 和候选复核。
    """

    current = service.get_authoring(workflow_uuid)
    draft = current["draft"]
    assert isinstance(draft, dict)
    saved = service.save_draft(
        workflow_uuid,
        python_source=python_source,
        expected_draft_hash=str(draft["draft_hash"]),
        expected_workflow_revision=int(current["workflow_revision"]),
    )
    candidate = saved["candidate"]
    assert isinstance(candidate, dict)
    return service.apply_authoring(
        workflow_uuid,
        candidate_hash=str(candidate["candidate_hash"]),
    )


def test_compatible_operation_apply_automatically_refreshes_clean_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """实验操作兼容更新后，干净引用方工作流自动采用新展开图。

    参数：``tmp_path`` 隔离真实领域包、工作流定义目录和库存库。返回：无；断言
    父修订自动推进、调用节点身份不变、子修订 pin 更新且父源码保持已应用。
    异常：组合根、源码协调或断言失败时由 pytest 报告。
    """

    reset_workflow_service_for_test()
    # 本测试验证生产调度允许同一工作流存在多个任务；develop 模式的单任务
    # 调试槽由独立合同测试覆盖。
    monkeypatch.setattr(
        WorkflowService,
        "_develop_execution_mode",
        staticmethod(lambda: False),
    )
    package_root = tmp_path / "editable"
    package_root.mkdir()
    _write_package(package_root)
    inventory_store = InventoryStore(str(tmp_path / "inventory.db"))
    try:
        service, _projection = compose_local_workflow_template_runtime(
            tmp_path,
            inventory_store=inventory_store,
            registry=_Registry(),
            editable_package_roots=(package_root,),
        )
        parent_before = service.get_graph(PARENT_WORKFLOW_UUID)
        child_before = service.get_graph(CHILD_WORKFLOW_UUID)
        parent_revision = int(parent_before["workflow"]["revision"])
        child_revision = int(child_before["workflow"]["revision"])
        task_before = service.create_workflow_task(
            workflow_uuid=PARENT_WORKFLOW_UUID,
            run_mode="normal",
            target_node_uuid=None,
            input_value={},
            description=None,
            meta_data={},
        )

        result = _apply_source(
            service,
            CHILD_WORKFLOW_UUID,
            _child_source().replace("Published child", "Published child v2"),
        )

        assert result["apply_result"]["warnings"] == []
        parent_after = service.get_graph(PARENT_WORKFLOW_UUID)
        child_after = service.get_graph(CHILD_WORKFLOW_UUID)
        assert int(child_after["workflow"]["revision"]) == child_revision + 1
        assert int(parent_after["workflow"]["revision"]) == parent_revision + 1
        invocation = next(
            node for node in parent_after["nodes"] if node["uuid"] == INVOCATION_UUID
        )
        composite = invocation["meta_data"]["unilab"]["composite"]
        assert composite["child_workflow_revision"] == child_revision + 1
        assert service.get_authoring(PARENT_WORKFLOW_UUID)["state"] == "applied"
        frozen_before = service.get_workflow_task(task_before["uuid"])[
            "workflow_snapshot"
        ]
        frozen_invocation = next(
            node for node in frozen_before["nodes"] if node["uuid"] == INVOCATION_UUID
        )
        assert (
            frozen_invocation["meta_data"]["unilab"]["composite"][
                "child_workflow_revision"
            ]
            == child_revision
        )

        task_after = service.create_workflow_task(
            workflow_uuid=PARENT_WORKFLOW_UUID,
            run_mode="normal",
            target_node_uuid=None,
            input_value={},
            description=None,
            meta_data={},
        )
        new_invocation = next(
            node
            for node in task_after["workflow_snapshot"]["nodes"]
            if node["uuid"] == INVOCATION_UUID
        )
        assert (
            new_invocation["meta_data"]["unilab"]["composite"][
                "child_workflow_revision"
            ]
            == child_revision + 1
        )
    finally:
        reset_workflow_service_for_test()
        inventory_store.close()


def test_child_updates_never_apply_parent_file_changed_before_watcher(
    tmp_path: Path,
) -> None:
    """父 Python 文件已变但 watcher 未同步时不得自动应用该用户编辑。

    参数：``tmp_path`` 隔离真实领域包、内存定义和库存库。返回：无；断言刷新前
    直接修改的引用方文件会被识别为未应用源码，实验操作更新只留下警告，父修订和
    已应用图均保持不变。异常：真实源码 CAS、编译或断言失败时由 pytest 报告。
    """

    reset_workflow_service_for_test()
    package_root = tmp_path / "editable"
    package_root.mkdir()
    _write_package(package_root)
    inventory_store = InventoryStore(str(tmp_path / "inventory.db"))
    try:
        service, _projection = compose_local_workflow_template_runtime(
            tmp_path,
            inventory_store=inventory_store,
            registry=_Registry(),
            editable_package_roots=(package_root,),
        )
        parent_before = service.get_graph(PARENT_WORKFLOW_UUID)
        parent_revision = int(parent_before["workflow"]["revision"])
        parent_source_path = package_root / "c1_product_lab" / "workflows" / "parent.py"
        parent_source_path.write_text(
            parent_source_path.read_text(encoding="utf-8")
            + "\n# 用户尚未确认应用的编辑\n",
            encoding="utf-8",
        )

        result = _apply_source(
            service,
            CHILD_WORKFLOW_UUID,
            _child_source().replace("Published child", "Published child v3"),
        )

        warnings = result["apply_result"]["warnings"]
        assert warnings == [
            {
                "code": "dependent_authoring_refresh_pending",
                "message": (
                    f"实验操作已更新，但引用方 {PARENT_WORKFLOW_UUID} 仍需处理兼容问题"
                ),
            }
        ]
        parent_after = service.get_graph(PARENT_WORKFLOW_UUID)
        assert int(parent_after["workflow"]["revision"]) == parent_revision
        # 只比较父工作流自身的已应用定义；读图中的模板目录投影会合法反映刚应用
        # 的子模板代际，但不能因此推进父修订或改写父节点/连线。
        assert parent_after["workflow"] == parent_before["workflow"]
        assert parent_after["nodes"] == parent_before["nodes"]
        assert parent_after["edges"] == parent_before["edges"]
        assert service.get_authoring(PARENT_WORKFLOW_UUID)["state"] in {
            "unapplied_source_only",
            "unapplied_graph",
        }
    finally:
        reset_workflow_service_for_test()
        inventory_store.close()
