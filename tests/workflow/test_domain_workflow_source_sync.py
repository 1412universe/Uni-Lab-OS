"""用户导入与 API 编辑同步到领域包 Python 权威的端到端测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from tests.workflow.test_authoring_engine import (
    PREPARE_NODE_UUID,
    WORKFLOW_UUID,
    _engine,
    _source,
    _template,
)
from unilabos.app.workflow_api import create_workflow_app
from unilabos.workflow.authoring_engine import WorkflowAuthoringEngine
from unilabos.workflow.authoring_kernel import AuthoringCatalogSnapshot
from unilabos.workflow.domain_source_target import DomainWorkflowSourceTarget
from unilabos.workflow.service import WorkflowConflict, WorkflowError, WorkflowService
from unilabos.workflow.source_discovery import discover_editable_sources
from unilabos.workflow.store import WorkflowStore


def _empty_domain_package(selected_root: Path) -> Path:
    """建立只有包身份、尚无工作流的合法领域包。"""

    package_root = selected_root / "demo_domain"
    package_root.mkdir(parents=True)
    selected_root.joinpath("package.yaml").write_text(
        "package:\n  name: demo_domain\nworkflows: []\n",
        encoding="utf-8",
    )
    return package_root


def _service(
    *,
    database_path: Path,
    selected_root: Path,
    engine: WorkflowAuthoringEngine | None = None,
) -> tuple[WorkflowService, WorkflowStore, WorkflowStore]:
    """按生产启动顺序装配一套隔离 Local 工作流服务。

    参数：``database_path`` 是仅保存 Task/Job 等运行事实的 SQLite 文件；
    ``selected_root`` 是含 ``package.yaml`` 的授权领域包入口；``engine`` 是可选
    AST 编译器，省略时使用测试目录。返回：服务、运行事实库和进程内定义目录。
    异常：发现、Python 激活或发布合同恢复失败时原样传播，且不会把工作流定义
    写入运行 SQLite。启动顺序固定为“发现并激活 Python 定义→恢复发布合同”。
    """

    engine = engine or _engine()
    plan = discover_editable_sources((selected_root,))
    runtime_store = WorkflowStore(
        database_path,
        persist_workflow_definitions=False,
    )
    definition_store = WorkflowStore(":memory:")
    service = WorkflowService(
        runtime_store,
        definition_store=definition_store,
        compiler=engine,
        source_target=DomainWorkflowSourceTarget.from_discovery_plan(plan),
    )
    service.replace_discovered_source_authorizations(plan)
    if plan.registrations:
        service.activate_registered_sources_to_fixed_point()
    service.restore_published_workflow_contracts()
    return service, runtime_store, definition_store


def test_python_import_and_api_edits_survive_restart_via_domain_source(
    tmp_path: Path,
) -> None:
    """Python 导入及后续元数据/图修改都应回写同一文件并可冷启动重建。

    参数：``tmp_path`` 是本用例独占的领域包与运行事实目录。返回：无。异常：
    导入、API 编辑、原子写回或重启恢复丢失任一工作流事实时由断言暴露。
    """

    selected_root = tmp_path / "domain"
    package_root = _empty_domain_package(selected_root)
    database_path = tmp_path / "workflow_history.db"
    service, runtime_store, definitions = _service(
        database_path=database_path,
        selected_root=selected_root,
    )
    try:
        imported = service.import_python_workflow(
            file_name="sample_workflow.py",
            python_source=_source().replace(
                '    description="Prepare and analyze one sample.",\n',
                '    description="Prepare and analyze one sample.",\n'
                '    workflow_type="experiment_operation",\n',
            ),
        )
        assert imported["workflow"]["uuid"] == WORKFLOW_UUID
        source_path = package_root / "experiment_operations" / "sample_workflow.py"
        assert source_path.is_file()
        manifest = yaml.safe_load(
            selected_root.joinpath("package.yaml").read_text(encoding="utf-8")
        )
        assert manifest["workflows"] == [
            {
                "workflow_uuid": WORKFLOW_UUID,
                "source": ("demo_domain/experiment_operations/sample_workflow.py"),
            }
        ]

        updated_workflow = service.update_workflow(
            WORKFLOW_UUID,
            name="领域包工作流",
            tags=["production", "domain"],
            description="修改后仍以领域 Python 为准",
            meta_data={"owner": "lab"},
            workflow_type="experiment_operation",
        )
        graph = service.get_graph(WORKFLOW_UUID)
        edited_nodes = []
        for node in graph["nodes"]:
            edited = dict(node)
            if edited["uuid"] == PREPARE_NODE_UUID:
                edited["name"] = "领域包加样"
                edited["description"] = "由工作台修改并回写"
            edited_nodes.append(edited)
        edited_graph = service.save_graph(
            WORKFLOW_UUID,
            revision=int(updated_workflow["revision"]),
            nodes=edited_nodes,
            edges=graph["edges"],
        )
        assert edited_graph["workflow"]["revision"] == 3
        source = source_path.read_text(encoding="utf-8")
        assert "displayname='领域包工作流'" in source
        assert "tags=['production', 'domain']" in source
        assert "workflow_type='experiment_operation'" in source
        assert "meta_data={'owner': 'lab'}" in source
        assert "# [领域包加样]: 由工作台修改并回写" in source
        assert runtime_store.count_rows("workflow") == 0
        assert definitions.count_rows("workflow") == 1
    finally:
        service.close()

    reopened, reopened_runtime, reopened_definitions = _service(
        database_path=database_path,
        selected_root=selected_root,
    )
    try:
        rebuilt = reopened.get_graph(WORKFLOW_UUID)
        assert rebuilt["workflow"]["name"] == "领域包工作流"
        assert rebuilt["workflow"]["tags"] == ["production", "domain"]
        assert rebuilt["workflow"]["workflow_type"] == "experiment_operation"
        assert rebuilt["workflow"]["meta_data"]["owner"] == "lab"
        assert (
            next(
                node for node in rebuilt["nodes"] if node["uuid"] == PREPARE_NODE_UUID
            )["name"]
            == "领域包加样"
        )
        assert reopened_runtime.count_rows("workflow") == 0
        assert reopened_definitions.count_rows("workflow") == 1
    finally:
        reopened.close()


def test_published_experiment_operation_survives_restart_in_domain_package(
    tmp_path: Path,
) -> None:
    """已发布实验操作的状态和合同须从领域包文件跨重启恢复。

    参数：``tmp_path`` 隔离领域包、内存定义目录和运行事实库。返回：无。异常：
    发布合同写入 SQLite、稳定身份变化或重启后降级为源码时由断言暴露。
    """

    selected_root = tmp_path / "domain"
    package_root = _empty_domain_package(selected_root)
    database_path = tmp_path / "workflow_history.db"
    service, runtime_store, _definitions = _service(
        database_path=database_path,
        selected_root=selected_root,
    )
    try:
        imported = service.import_python_workflow(
            file_name="published_operation.py",
            python_source=_source().replace(
                '    description="Prepare and analyze one sample.",\n',
                '    description="Prepare and analyze one sample.",\n'
                '    workflow_type="experiment_operation",\n',
            ),
        )
        first_contract = service.publish_workflow_contract(
            WORKFLOW_UUID,
            revision=int(imported["workflow"]["revision"]),
        )
        repeated_contract = service.publish_workflow_contract(
            WORKFLOW_UUID,
            revision=int(imported["workflow"]["revision"]),
        )
        assert repeated_contract["uuid"] == first_contract["uuid"]
        updated = service.update_workflow(
            WORKFLOW_UUID,
            name="已更新实验操作",
            tags=[],
            description="第二个不可变发布版本",
            meta_data={},
            workflow_type="experiment_operation",
        )
        contract = service.publish_workflow_contract(
            WORKFLOW_UUID,
            revision=int(updated["revision"]),
        )
        assert contract["uuid"] != first_contract["uuid"]
        assert contract["version"] == 2
        assert service.get_workflow(WORKFLOW_UUID)["status"] == "published"
        publication_path = package_root / "workflow_publications.json"
        assert publication_path.is_file()
        assert (
            len(
                yaml.safe_load(publication_path.read_text(encoding="utf-8"))[
                    "publications"
                ]
            )
            == 2
        )
        assert runtime_store.count_rows("workflow") == 0
        with runtime_store.read() as connection:
            assert (
                connection.execute(
                    """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'published_workflow_contract'
                """
                ).fetchone()
                is None
            )
    finally:
        service.close()

    reopened, reopened_runtime, _reopened_definitions = _service(
        database_path=database_path,
        selected_root=selected_root,
    )
    try:
        assert reopened.get_workflow(WORKFLOW_UUID)["status"] == "published"
        listed = reopened.list_published_workflow_contracts()
        assert listed["items"][0]["uuid"] == contract["uuid"]
        assert (
            listed["items"][0]["node_template_uuid"] == contract["node_template_uuid"]
        )
        assert reopened_runtime.count_rows("workflow") == 0
        with reopened_runtime.read() as connection:
            assert (
                connection.execute(
                    """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'published_workflow_contract'
                """
                ).fetchone()
                is None
            )
    finally:
        reopened.close()


def test_http_create_persists_workflow_types_in_separate_domain_directories(
    tmp_path: Path,
) -> None:
    """证明接口创建的普通工作流与实验操作分别写入领域包并可重启恢复。

    参数：``tmp_path`` 隔离唯一领域包、运行事实库和进程内定义目录。返回：无。
    异常：HTTP 创建没有生成规范 Python、目录分类错误、清单缺项或冷启动丢失
    任一工作流时由断言暴露。
    """

    selected_root = tmp_path / "domain"
    package_root = _empty_domain_package(selected_root)
    database_path = tmp_path / "workflow_history.db"
    service, runtime_store, _definitions = _service(
        database_path=database_path,
        selected_root=selected_root,
    )
    # 两个响应 UUID 是工作流（Workflow）的稳定身份，同时决定默认 Python
    # 文件名；目录只表达源码类别，不参与身份计算。
    client = TestClient(create_workflow_app(service))
    try:
        normal_response = client.post(
            "/api/v1/workflows",
            json={
                "name": "普通工作流",
                "tags": [],
                "description": "接口创建的普通流程",
                "meta_data": {},
            },
        )
        operation_response = client.post(
            "/api/v1/workflows",
            json={
                "name": "实验操作",
                "workflow_type": "experiment_operation",
                "tags": [],
                "description": "接口创建的实验操作",
                "meta_data": {},
            },
        )
        assert normal_response.status_code == 201, normal_response.text
        assert operation_response.status_code == 201, operation_response.text
        normal = normal_response.json()["data"]
        operation = operation_response.json()["data"]
        normal_name = DomainWorkflowSourceTarget.default_file_name(normal["uuid"])
        operation_name = DomainWorkflowSourceTarget.default_file_name(operation["uuid"])
        normal_path = package_root / "workflows" / normal_name
        operation_path = package_root / "experiment_operations" / operation_name

        assert normal_path.is_file()
        assert operation_path.is_file()
        normal_source = normal_path.read_text(encoding="utf-8")
        operation_source = operation_path.read_text(encoding="utf-8")
        assert "@workflow(" in normal_source
        assert f'workflow_uuid="{normal["uuid"]}"' in normal_source
        assert "displayname='普通工作流'" in normal_source
        assert "description='接口创建的普通流程'" in normal_source
        assert "@workflow(" in operation_source
        assert f'workflow_uuid="{operation["uuid"]}"' in operation_source
        assert "displayname='实验操作'" in operation_source
        assert "description='接口创建的实验操作'" in operation_source
        assert "workflow_type='experiment_operation'" in operation_source

        manifest = yaml.safe_load(
            selected_root.joinpath("package.yaml").read_text(encoding="utf-8")
        )
        assert manifest["workflows"] == [
            {
                "workflow_uuid": normal["uuid"],
                "source": f"demo_domain/workflows/{normal_name}",
            },
            {
                "workflow_uuid": operation["uuid"],
                "source": (f"demo_domain/experiment_operations/{operation_name}"),
            },
        ]
        assert runtime_store.count_rows("workflow") == 0
    finally:
        service.close()

    reopened, reopened_runtime, _reopened_definitions = _service(
        database_path=database_path,
        selected_root=selected_root,
    )
    try:
        reopened_normal = reopened.get_workflow(normal["uuid"])
        reopened_operation = reopened.get_workflow(operation["uuid"])
        assert reopened_normal["workflow_type"] == "normal"
        assert reopened_normal["description"] == "接口创建的普通流程"
        assert reopened_operation["workflow_type"] == "experiment_operation"
        assert reopened_operation["description"] == "接口创建的实验操作"
        assert reopened_runtime.count_rows("workflow") == 0
    finally:
        reopened.close()


def test_http_create_rejects_duplicate_authoring_function_name(
    tmp_path: Path,
) -> None:
    """接口创建的工作流名称映射为作者函数名时必须保持包内唯一。"""

    selected_root = tmp_path / "domain"
    _empty_domain_package(selected_root)
    service, _runtime_store, _definitions = _service(
        database_path=tmp_path / "workflow_history.db",
        selected_root=selected_root,
    )
    client = TestClient(create_workflow_app(service))
    body = {
        "name": "重复函数名",
        "tags": [],
        "description": None,
        "meta_data": {},
    }
    try:
        first = client.post("/api/v1/workflows", json=body)
        second = client.post("/api/v1/workflows", json=body)
        assert first.status_code == 201, first.text
        assert second.status_code == 200
        assert second.json()["code"] == 3003
    finally:
        service.close()


def test_json_import_is_canonicalized_to_domain_python(tmp_path: Path) -> None:
    """旧 JSON 图导入后应只留下规范 Python，并可从该文件冷启动重建。"""

    selected_root = tmp_path / "domain"
    package_root = _empty_domain_package(selected_root)
    template, handles = _template(
        "30000000-0000-4000-8000-000000000090",
        name="noop",
        handles=[],
    )
    engine = WorkflowAuthoringEngine(
        catalog=AuthoringCatalogSnapshot.from_entities([template], handles)
    )
    service, runtime_store, definitions = _service(
        database_path=tmp_path / "workflow_history.db",
        selected_root=selected_root,
        engine=engine,
    )
    source = f'''from lab.devices import Reactor
from unilabos.workflow.authoring import device, workflow, workflow_output

reactor: Reactor = device()

@workflow(workflow_uuid="{WORKFLOW_UUID}", displayname="JSON source")
def json_source():
    # unilab:node_uuid={PREPARE_NODE_UUID}
    completed = reactor.noop()
    return workflow_output()
'''
    compiled = engine.compile(
        workflow_uuid=WORKFLOW_UUID,
        workflow_revision=7,
        python_source=source,
        source_uri="test://json-source",
        applied_graph={
            "workflow": {
                "uuid": WORKFLOW_UUID,
                "name": "base",
                "tags": [],
                "description": None,
                "meta_data": {},
                "revision": 7,
            },
            "nodes": [],
            "edges": [],
            "node_templates": [],
            "handle_templates": [],
        },
    )
    assert compiled.valid and compiled.graph is not None
    with definitions.transaction() as connection:
        definitions._ensure_authoring_catalog_projection(
            connection,
            node_templates=compiled.graph["node_templates"],
            handle_templates=compiled.graph["handle_templates"],
            authority_id=compiled.template_catalog_fingerprint,
            now="2026-08-28T00:00:00+00:00",
        )
    identity = ""
    source_path: Path | None = None
    try:
        imported = service.import_legacy_workflow(
            payload={
                "workflow_name": "JSON 导入工作流",
                "tags": ["json"],
                "description": "导入后转换为 Python",
                "meta_data": {"owner": "lab"},
                "nodes": [
                    {key: value for key, value in node.items() if key != "type"}
                    for node in compiled.graph["nodes"]
                ],
                "edges": compiled.graph["edges"],
            }
        )
        identity = imported["workflow"]["uuid"]
        source_path = (
            package_root / "workflows" / (f"workflow_{identity.replace('-', '')}.py")
        )
        source = source_path.read_text(encoding="utf-8")
        assert "@workflow(" in source
        assert "displayname='JSON 导入工作流'" in source
        assert "tags=['json']" in source
        assert runtime_store.count_rows("workflow") == 0
    finally:
        service.close()

    assert identity and source_path is not None
    reopened, reopened_runtime, reopened_definitions = _service(
        database_path=tmp_path / "workflow_history.db",
        selected_root=selected_root,
        engine=engine,
    )
    try:
        rebuilt = reopened.get_graph(identity)
        assert rebuilt["workflow"]["name"] == "JSON 导入工作流"
        assert rebuilt["workflow"]["tags"] == ["json"]
        assert rebuilt["workflow"]["meta_data"]["owner"] == "lab"
        assert reopened_runtime.count_rows("workflow") == 0
        assert reopened_definitions.count_rows("workflow") == 1
    finally:
        reopened.close()


def test_imported_workflow_delete_removes_source_and_manifest(
    tmp_path: Path,
) -> None:
    """删除领域工作流应同时移除源码文件和 manifest 登记。"""

    selected_root = tmp_path / "domain"
    package_root = _empty_domain_package(selected_root)
    database_path = tmp_path / "workflow_history.db"
    service, _runtime_store, _definitions = _service(
        database_path=database_path,
        selected_root=selected_root,
    )
    source_path = package_root / "workflows" / "sample_workflow.py"
    try:
        service.import_python_workflow(
            file_name=source_path.name,
            python_source=_source(),
        )
        service.delete_workflow(WORKFLOW_UUID)
        assert not source_path.exists()
        assert (
            yaml.safe_load(
                selected_root.joinpath("package.yaml").read_text(encoding="utf-8")
            )["workflows"]
            == []
        )
        with pytest.raises(WorkflowError) as deleted:
            service.get_workflow(WORKFLOW_UUID)
        assert deleted.value.code == "not_found"
    finally:
        service.close()

    reopened, _reopened_runtime, reopened_definitions = _service(
        database_path=database_path,
        selected_root=selected_root,
    )
    try:
        with pytest.raises(WorkflowError) as deleted:
            reopened.get_workflow(WORKFLOW_UUID)
        assert deleted.value.code == "not_found"
        assert reopened_definitions.count_rows("workflow") == 0
    finally:
        reopened.close()


def test_deleted_python_workflow_can_be_imported_again_with_same_identity(
    tmp_path: Path,
) -> None:
    """删除后再次导入同一 Python 文件不得被旧 UUID/图记录阻塞。"""

    selected_root = tmp_path / "domain"
    package_root = _empty_domain_package(selected_root)
    service, _runtime_store, definitions = _service(
        database_path=tmp_path / "workflow_history.db",
        selected_root=selected_root,
    )
    try:
        first = service.import_python_workflow(
            file_name="same_workflow.py",
            python_source=_source(),
        )
        service.delete_workflow(first["workflow"]["uuid"])
        assert definitions.count_rows("workflow", include_deleted=True) == 0
        second = service.import_python_workflow(
            file_name="same_workflow.py",
            python_source=_source(),
        )
        assert second["workflow"]["uuid"] == first["workflow"]["uuid"]
        assert second["workflow"]["revision"] == 1
        assert definitions.count_rows("workflow") == 1
        assert package_root.joinpath(
            "workflows", "same_workflow.py"
        ).is_file()
    finally:
        service.close()


def test_domain_workflow_status_returns_source_after_editing_published_revision(
    tmp_path: Path,
) -> None:
    """领域包发布旧修订后，图被修改时当前定义必须回到 source。"""

    selected_root = tmp_path / "domain"
    _empty_domain_package(selected_root)
    service, _runtime_store, _definitions = _service(
        database_path=tmp_path / "workflow_history.db",
        selected_root=selected_root,
    )
    try:
        imported = service.import_python_workflow(
            file_name="published_then_edited.py",
            python_source=_source(),
        )
        service.publish_workflow_contract(
            WORKFLOW_UUID,
            revision=int(imported["workflow"]["revision"]),
        )
        graph = service.get_graph(WORKFLOW_UUID)
        edited_nodes = [
            {**node, "name": "编辑后的节点"} for node in graph["nodes"]
        ]
        edited = service.save_graph(
            WORKFLOW_UUID,
            revision=int(imported["workflow"]["revision"]),
            nodes=edited_nodes,
            edges=graph["edges"],
        )
        assert edited["workflow"]["revision"] == int(imported["workflow"]["revision"]) + 1
        assert service.get_workflow(WORKFLOW_UUID)["status"] == "source"
        assert service.list_workflows(
            workflow_type="experiment_operation",
            status="published",
        )["items"] == []
    finally:
        service.close()


def test_delete_hides_published_contract_when_history_cleanup_is_unavailable(
    tmp_path: Path,
) -> None:
    """定义删除完成后，暂时无法清理的发布历史不得继续对外可见。

    参数：``tmp_path`` 隔离领域包、运行事实库和故障文件。返回：无。异常：发布
    文件故障让删除表面失败、合同仍可查询或重启后恢复孤儿定义时由断言暴露。
    """

    selected_root = tmp_path / "domain"
    package_root = _empty_domain_package(selected_root)
    database_path = tmp_path / "workflow_history.db"
    service, _runtime_store, _definitions = _service(
        database_path=database_path,
        selected_root=selected_root,
    )
    publication_path = package_root / "workflow_publications.json"
    try:
        imported = service.import_python_workflow(
            file_name="published_operation.py",
            python_source=_source().replace(
                '    description="Prepare and analyze one sample.",\n',
                '    description="Prepare and analyze one sample.",\n'
                '    workflow_type="experiment_operation",\n',
            ),
        )
        service.publish_workflow_contract(
            WORKFLOW_UUID,
            revision=int(imported["workflow"]["revision"]),
        )
        stale_publications = publication_path.read_bytes()
        publication_path.unlink()
        publication_path.mkdir()

        service.delete_workflow(WORKFLOW_UUID)

        with pytest.raises(WorkflowError) as deleted:
            service.get_workflow(WORKFLOW_UUID)
        assert deleted.value.code == "not_found"
        assert service.list_published_workflow_contracts()["items"] == []
        assert (
            yaml.safe_load(
                selected_root.joinpath("package.yaml").read_text(encoding="utf-8")
            )["workflows"]
            == []
        )
    finally:
        service.close()

    publication_path.rmdir()
    publication_path.write_bytes(stale_publications)
    reopened, _reopened_runtime, reopened_definitions = _service(
        database_path=database_path,
        selected_root=selected_root,
    )
    try:
        with pytest.raises(WorkflowError) as deleted:
            reopened.get_workflow(WORKFLOW_UUID)
        assert deleted.value.code == "not_found"
        assert reopened.list_published_workflow_contracts()["items"] == []
        assert reopened_definitions.count_rows("workflow") == 0
    finally:
        reopened.close()


def test_python_import_identity_conflict_does_not_publish_manifest(
    tmp_path: Path,
) -> None:
    """内存定义已占用 UUID 时，失败导入不得提前登记领域源码。"""

    selected_root = tmp_path / "domain"
    package_root = _empty_domain_package(selected_root)
    service, _runtime_store, definitions = _service(
        database_path=tmp_path / "workflow_history.db",
        selected_root=selected_root,
    )
    definitions.create_workflow(
        workflow_uuid=WORKFLOW_UUID,
        name="既有内存工作流",
        tags=[],
        description=None,
        meta_data={},
    )
    try:
        with pytest.raises(WorkflowConflict):
            service.import_python_workflow(
                file_name="conflicting_workflow.py",
                python_source=_source(),
            )
        assert (
            yaml.safe_load(
                selected_root.joinpath("package.yaml").read_text(encoding="utf-8")
            )["workflows"]
            == []
        )
        assert not package_root.joinpath(
            "workflows", "conflicting_workflow.py"
        ).exists()
    finally:
        service.close()


def test_python_import_rolls_back_manifest_when_activation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """源码发布后的激活失败应补偿 manifest 和内存定义，不留下假导入。"""

    selected_root = tmp_path / "domain"
    package_root = _empty_domain_package(selected_root)
    service, _runtime_store, definitions = _service(
        database_path=tmp_path / "workflow_history.db",
        selected_root=selected_root,
    )
    publish = service._publish_imported_domain_workflow

    def fail_activation(**_kwargs: object) -> dict[str, object]:
        raise WorkflowError("internal_error")

    monkeypatch.setattr(
        service,
        "_publish_imported_domain_workflow",
        fail_activation,
    )
    try:
        with pytest.raises(WorkflowError) as failed:
            service.import_python_workflow(
                file_name="failed_activation.py",
                python_source=_source(),
            )
        assert failed.value.code == "internal_error"
        assert (
            yaml.safe_load(
                selected_root.joinpath("package.yaml").read_text(encoding="utf-8")
            )["workflows"]
            == []
        )
        assert definitions.count_rows("workflow") == 0
        assert package_root.joinpath("workflows", "failed_activation.py").is_file()

        monkeypatch.setattr(
            service,
            "_publish_imported_domain_workflow",
            publish,
        )
        retried = service.import_python_workflow(
            file_name="failed_activation.py",
            python_source=_source(),
        )
        assert retried["workflow"]["uuid"] == WORKFLOW_UUID
        assert retried["workflow"]["revision"] == 1
        assert service.get_authoring(WORKFLOW_UUID)["state"] == "applied"
    finally:
        service.close()
