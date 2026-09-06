from copy import deepcopy

import pytest

from unilabos.workflow.execution_plan import ExecutionPlanBuilder
from unilabos.workflow.task_input import TaskInputError, prepare_task_input
from unilabos.workflow.workflow_io import (
    WorkflowIOValidationError,
    validate_workflow_graph_io,
)

from .test_f05_material_source_authoring import (
    _compile,
    _source,
    MATERIAL_SOURCE_NODE_UUID,
)

SITE_UUID = "60000000-0000-4000-8000-000000000010"


def selected_graph():
    """编译最小启动库位来源，保持公共作者入口可验证。"""
    source = (
        _source()
        .replace("def material_assay():", "def material_assay(*, source_site: str):")
        .replace("site=None,", "site=source_site,")
    )
    compiled = _compile(source)
    assert compiled.valid, compiled.diagnostics
    return compiled.graph


@pytest.mark.parametrize(
    "bad_input", [{}, {"source_site": ""}, {"source_site": None}, {"source_site": 3}]
)
def test_missing_or_invalid_site_never_creates_a_broad_source(bad_input):
    graph = selected_graph()
    plan, jobs = ExecutionPlanBuilder().build(
        graph, run_mode="full", target_node_uuid=None
    )
    original = deepcopy(plan)
    with pytest.raises(TaskInputError):
        prepare_task_input(
            graph=graph,
            raw_input=bad_input,
            execution_plan=plan,
            jobs=jobs,
            site_selection_resolver=lambda _: {"site_uuids": [SITE_UUID]},
        )
    assert plan == original


@pytest.mark.parametrize(
    "binding",
    [
        {"parameter": "unknown"},
        {"parameter": 1},
        {"parameter": "source_site", "extra": True},
    ],
)
def test_direct_graph_rejects_invalid_source_site_binding(binding):
    graph = selected_graph()
    node = next(
        node for node in graph["nodes"] if node["uuid"] == MATERIAL_SOURCE_NODE_UUID
    )
    node["meta_data"]["unilab"]["material_source_site_binding"] = binding
    with pytest.raises(WorkflowIOValidationError):
        validate_workflow_graph_io(graph)


def test_selected_site_cannot_escape_declared_candidate_range():
    graph = selected_graph()
    node = next(
        node for node in graph["nodes"] if node["uuid"] == MATERIAL_SOURCE_NODE_UUID
    )
    node["param"]["slot_range"] = ["60000000-0000-4000-8000-000000000011"]
    plan, jobs = ExecutionPlanBuilder().build(
        graph, run_mode="full", target_node_uuid=None
    )
    with pytest.raises(TaskInputError, match="不在工作流允许范围"):
        prepare_task_input(
            graph=graph,
            raw_input={"source_site": "A1"},
            execution_plan=plan,
            jobs=jobs,
            site_selection_resolver=lambda _: {"site_uuids": [SITE_UUID]},
        )


def test_unresolved_site_fails_before_task_creation():
    graph = selected_graph()
    plan, jobs = ExecutionPlanBuilder().build(
        graph, run_mode="full", target_node_uuid=None
    )
    with pytest.raises(TaskInputError, match="缺少库存权威"):
        prepare_task_input(
            graph=graph, raw_input={"source_site": "A1"}, execution_plan=plan, jobs=jobs
        )
