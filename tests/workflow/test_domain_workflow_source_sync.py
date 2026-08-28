"""用户导入与 API 编辑同步到领域包 Python 权威的端到端测试。"""

from __future__ import annotations

from pathlib import Path

import yaml

from tests.workflow.test_authoring_engine import (
    PREPARE_NODE_UUID,
    WORKFLOW_UUID,
    _engine,
    _source,
    _template,
)
from unilabos.workflow.authoring_engine import WorkflowAuthoringEngine
from unilabos.workflow.authoring_kernel import AuthoringCatalogSnapshot
from unilabos.workflow.domain_source_target import DomainWorkflowSourceTarget
from unilabos.workflow.source_discovery import discover_editable_sources
from unilabos.workflow.store import WorkflowStore
from unilabos.workflow.service import WorkflowService


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
    """从当前 manifest 构造运行事实库、内存定义目录和领域源码目标。"""

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
    return service, runtime_store, definition_store


def test_python_import_and_api_edits_survive_restart_via_domain_source(
    tmp_path: Path,
) -> None:
    """Python 导入及后续元数据/图修改都应回写同一文件并可冷启动重建。"""

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
            python_source=_source(),
        )
        assert imported["workflow"]["uuid"] == WORKFLOW_UUID
        source_path = package_root / "workflows" / "sample_workflow.py"
        assert source_path.is_file()
        manifest = yaml.safe_load(
            selected_root.joinpath("package.yaml").read_text(encoding="utf-8")
        )
        assert manifest["workflows"] == [
            {
                "workflow_uuid": WORKFLOW_UUID,
                "source": "demo_domain/workflows/sample_workflow.py",
            }
        ]

        updated_workflow = service.update_workflow(
            WORKFLOW_UUID,
            name="领域包工作流",
            tags=["production", "szlab"],
            description="修改后仍以领域 Python 为准",
            meta_data={"owner": "lab"},
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
        assert "tags=['production', 'szlab']" in source
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
        assert rebuilt["workflow"]["tags"] == ["production", "szlab"]
        assert rebuilt["workflow"]["meta_data"]["owner"] == "lab"
        assert next(
            node for node in rebuilt["nodes"] if node["uuid"] == PREPARE_NODE_UUID
        )["name"] == "领域包加样"
        assert reopened_runtime.count_rows("workflow") == 0
        assert reopened_definitions.count_rows("workflow") == 1
    finally:
        reopened.close()


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
        source_path = package_root / "workflows" / (
            f"workflow_{identity.replace('-', '')}.py"
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
