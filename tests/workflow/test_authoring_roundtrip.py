"""可信工作流创作的源码/候选图固定点测试。"""

from __future__ import annotations

import ast
from copy import deepcopy

import pytest

from unilabos.workflow.authoring_engine import WorkflowAuthoringEngine
from unilabos.workflow.models import CandidateChangeset

from .test_authoring_engine import (
    ANALYZE_NODE_UUID,
    PREPARE_NODE_UUID,
    WORKFLOW_UUID,
    _compile,
    _engine,
)


@pytest.fixture()
def authoring_engine() -> WorkflowAuthoringEngine:
    """向往返测试提供隔离的工作流创作编译器。"""

    return _engine()


def _typed_result_source() -> str:
    """返回使用 ``TypedDict`` 结果记录的等价作者源码。"""

    return f'''from typing import Annotated, Literal, TypedDict

from pydantic import Field
from lab.devices import Reactor
from unilabos.registry.placeholder_type import ResourceSlot
from unilabos.workflow.authoring import device, workflow


class SamplePreparationResult(TypedDict):
    sample: ResourceSlot
    report: Annotated[str, Field(unit="text")]


reactor: Reactor = device()


@workflow(
    workflow_uuid="{WORKFLOW_UUID}",
    displayname="Sample preparation",
    description="Prepare and analyze one sample.",
)
def prepare_sample(
    *,
    sample: ResourceSlot,
    cycles: Annotated[int, Field(unit="cycle", ge=1, le=10)] = 3,
    mode: Literal["fast", "safe"] = "safe",
) -> SamplePreparationResult:
    # unilab:node_uuid={PREPARE_NODE_UUID}
    prepared = reactor.prepare(sample=sample, cycles=cycles)
    # unilab:node_uuid={ANALYZE_NODE_UUID}
    analyzed = reactor.analyze(prepared=prepared.prepared, label=mode)
    return {{"sample": prepared.prepared, "report": analyzed.report}}
'''


def test_compile_generate_compile_reaches_semantic_fixed_point(
    authoring_engine: WorkflowAuthoringEngine,
) -> None:
    """Python→候选图→Python→候选图必须达到语义固定点。"""

    compiled = _compile(authoring_engine)
    assert compiled.valid and compiled.graph is not None
    generated = authoring_engine.generate_python(
        workflow_uuid=WORKFLOW_UUID,
        workflow_revision=7,
        graph=compiled.graph,
        source_uri="package://lab/workflows/generated.py",
    )
    assert generated.valid and generated.normalized_python_source is not None
    repeated = _compile(
        authoring_engine,
        generated.normalized_python_source,
        graph=compiled.graph,
    )

    assert repeated.valid
    assert repeated.graph == compiled.graph
    assert repeated.normalized_python_source == generated.normalized_python_source
    assert CandidateChangeset.model_validate(repeated.changeset).kind == "source_only"


def test_typed_result_record_is_the_canonical_round_trip_form(
    authoring_engine: WorkflowAuthoringEngine,
) -> None:
    """显式结果记录应保留类型并成为确定性生成的规范形式。"""

    compiled = _compile(authoring_engine, _typed_result_source())

    assert compiled.valid and compiled.graph is not None, compiled.diagnostics
    assert compiled.normalized_python_source is not None
    module = ast.parse(compiled.normalized_python_source)
    result_records = [
        statement
        for statement in module.body
        if isinstance(statement, ast.ClassDef)
    ]
    assert [record.name for record in result_records] == ["SamplePreparationResult"]
    output_contract = compiled.graph["workflow"]["meta_data"]["unilab"][
        "output_contract"
    ]
    assert output_contract["outputs"][1]["unit"] == "text"
    input_contract = compiled.graph["workflow"]["meta_data"]["unilab"][
        "input_contract"
    ]
    assert input_contract["parameters"][1]["unit"] == "cycle"
    workflow_function = next(
        statement
        for statement in module.body
        if isinstance(statement, ast.FunctionDef)
    )
    assert ast.unparse(workflow_function.returns) == "SamplePreparationResult"
    assert isinstance(workflow_function.body[-1], ast.Return)
    assert isinstance(workflow_function.body[-1].value, ast.Dict)

    repeated = _compile(
        authoring_engine,
        compiled.normalized_python_source,
        graph=compiled.graph,
    )
    assert repeated.valid
    assert repeated.graph == compiled.graph
    assert CandidateChangeset.model_validate(repeated.changeset).kind == "source_only"


def test_validate_rejects_source_graph_mismatch(
    authoring_engine: WorkflowAuthoringEngine,
) -> None:
    """公共验证接口必须拒绝源码与候选图的语义分叉。"""

    compiled = _compile(authoring_engine)
    assert compiled.valid and compiled.graph is not None
    forged = deepcopy(compiled.graph)
    forged["nodes"][0]["param"] = {"cycles": 9}

    result = authoring_engine.validate(
        workflow_uuid=WORKFLOW_UUID,
        workflow_revision=7,
        graph=forged,
        python_source=compiled.normalized_python_source or "",
        source_uri="package://lab/workflows/sample.py",
    )

    assert not result.valid
    assert any(item["code"] == "round_trip_mismatch" for item in result.diagnostics)


def test_same_template_nodes_with_duplicate_result_names_are_uniquified() -> None:
    """同模板同结果变量名的两个节点必须自动去重，而不是被判为非法。

    场景：编辑器里拖入两个「取料」节点，只有参数不同。节点身份是 UUID，结果
    变量只是同一次生成里的 Python 局部名，所以这里只该去重，不该拒绝保存。
    后缀必须取自节点 UUID，保证重复渲染同一张图得到同一份源码。
    """

    from unilabos.workflow.authoring_python import _allocate_result_name

    def node(node_uuid: str, result_name: str) -> dict[str, object]:
        return {
            "uuid": node_uuid,
            "name": result_name,
            "meta_data": {"unilab": {"authoring_result_name": result_name}},
        }

    names: set[str] = set()
    by_uuid: dict[str, str] = {}
    first = _allocate_result_name(
        node("11111111-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "pick"),
        result_names=names,
        result_name_by_uuid=by_uuid,
    )
    second = _allocate_result_name(
        node("22222222-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "pick"),
        result_names=names,
        result_name_by_uuid=by_uuid,
    )

    # 先出现的节点保留原名，后出现的带 UUID 前缀后缀
    assert first == "pick"
    assert second == "pick_22222222"
    assert len(names) == 2

    # 同一节点再次分配必须返回缓存名，不得继续加后缀
    again = _allocate_result_name(
        node("22222222-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "pick"),
        result_names=names,
        result_name_by_uuid=by_uuid,
    )
    assert again == second
    assert len(names) == 2

    # 第三个同名节点仍然唯一
    third = _allocate_result_name(
        node("33333333-cccc-4ccc-8ccc-cccccccccccc", "pick"),
        result_names=names,
        result_name_by_uuid=by_uuid,
    )
    assert len({first, second, third}) == 3
