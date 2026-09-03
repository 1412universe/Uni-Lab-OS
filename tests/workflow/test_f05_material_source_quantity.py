"""试剂来源的数量声明：解析、编译为库存需求、规范源码往返。"""

from __future__ import annotations

import pytest

from unilabos.workflow.authoring_ast import parse_authoring_source
from unilabos.workflow.authoring_material import MaterialAuthoringError

from .test_authoring_engine import WORKFLOW_UUID, _applied_graph
from .test_f05_material_source_authoring import (
    MATERIAL_SOURCE_NODE_UUID,
    MOUNT_MATERIAL_UUID,
    PREPARE_NODE_UUID,
)
from .test_f05_material_source_authoring_roundtrip import _goal_material_engine


def _source(
    *,
    role: str = "REAGENT",
    quantity_arguments: str = "quantity=10, quantity_unit='mL', reagent_cas='64-17-5'",
    consumed: bool = True,
) -> str:
    """生成带可选数量声明的物料来源工作流源码。

    参数：``role`` 是物料流角色成员名；``quantity_arguments`` 是追加在选择器
    之后的关键字文本；``consumed`` 为假时不把来源交给任何动作。返回：静态
    Python 文本。异常：无。
    """

    consume_line = (
        f"    # unilab:node_uuid={PREPARE_NODE_UUID}\n"
        "    processed_plate = reactor.process_plate(plate=plate)\n"
        if consumed
        else ""
    )
    quantity_text = f", {quantity_arguments}" if quantity_arguments else ""
    return f'''from lab.devices import Reactor
from lab.resources import plate_96
from unilabos.workflow.authoring import (
    MaterialCustodyPolicy,
    MaterialFlowRole,
    device,
    material_source,
    resource_ref,
    workflow,
    workflow_output,
)


reactor: Reactor = device()


@workflow(workflow_uuid="{WORKFLOW_UUID}", displayname="Reagent quantity")
def reagent_quantity():
    # unilab:node_uuid={MATERIAL_SOURCE_NODE_UUID}
    plate = material_source(resource_template=plate_96, mode="existing", mount=resource_ref("{MOUNT_MATERIAL_UUID}"), material_uuid=None, site=None, slot_range=None, flow_role=MaterialFlowRole.{role}, custody_policy=MaterialCustodyPolicy.SHARED_SOURCE{quantity_text})
{consume_line}    return workflow_output()
'''


ETHANOL_INFO_UUID = "0f9c2d2e-6b1a-4c7e-9a0b-6d1e2f3a4b5c"


class _ReagentCatalog:
    """只读试剂目录桩：只认识乙醇的 CAS。"""

    def resolve_reagent_cas(self, cas: str) -> str | None:
        return ETHANOL_INFO_UUID if cas == "64-17-5" else None


def _compile(source: str, *, catalog: object | None = _ReagentCatalog()):
    engine = _goal_material_engine()
    engine._reagent_reference_resolver = catalog  # 测试专用：复用公共 goal 引擎目录
    return engine.compile(
        workflow_uuid=WORKFLOW_UUID,
        workflow_revision=7,
        python_source=source,
        source_uri="package://lab/workflows/reagent_quantity.py",
        applied_graph=_applied_graph(),
    )


def test_quantity_keywords_compile_to_one_inventory_requirement() -> None:
    """声明数量的试剂来源应编译为一条由首个消费动作承担的库存需求。"""

    compiled = _compile(_source())

    assert compiled.valid, compiled.diagnostics
    assert compiled.graph is not None
    requirements = compiled.graph["inventory_requirements"]
    assert len(requirements) == 1
    requirement = requirements[0]
    assert requirement["requirement_key"] == "plate"
    assert requirement["consume_node_uuid"] == PREPARE_NODE_UUID
    assert requirement["target_type"] == "reagent_info"
    assert requirement["reagent_info_uuid"] == ETHANOL_INFO_UUID
    assert requirement["required_quantity"] == 10.0
    assert requirement["quantity_unit"] == "mL"
    assert requirement["allow_split"] is False
    assert requirement["meta_data"] == {
        "material_source_node_uuid": MATERIAL_SOURCE_NODE_UUID,
        "reagent_cas": "64-17-5",
    }
    # 身份由工作流与来源节点确定性派生，重复编译不会产生新需求 UUID。
    again = _compile(_source())
    assert again.graph is not None
    assert again.graph["inventory_requirements"][0]["uuid"] == requirement["uuid"]

    node = next(
        item
        for item in compiled.graph["nodes"]
        if item["uuid"] == MATERIAL_SOURCE_NODE_UUID
    )
    assert node["meta_data"]["unilab"]["quantity_requirement"] == {
        "required_quantity": 10.0,
        "quantity_unit": "mL",
        "reagent_cas": "64-17-5",
        "reagent_info_uuid": ETHANOL_INFO_UUID,
    }
    # 选择器合同不变：数量不进入 param。
    assert "quantity" not in node["param"]


def test_source_without_quantity_compiles_without_requirements() -> None:
    """不声明数量的来源保持既有行为：没有库存需求，也没有数量元数据。"""

    compiled = _compile(_source(quantity_arguments=""))

    assert compiled.valid, compiled.diagnostics
    assert compiled.graph is not None
    assert compiled.graph["inventory_requirements"] == []
    node = next(
        item
        for item in compiled.graph["nodes"]
        if item["uuid"] == MATERIAL_SOURCE_NODE_UUID
    )
    assert "quantity_requirement" not in node["meta_data"]["unilab"]


def test_quantity_round_trips_through_normalized_python() -> None:
    """规范源码回写必须保留数量关键字，且整数量不带小数点。"""

    compiled = _compile(
        _source(quantity_arguments="quantity=10.0, quantity_unit='mL', reagent_cas='64-17-5'")
    )

    assert compiled.valid, compiled.diagnostics
    assert compiled.normalized_python_source is not None
    assert (
        "quantity=10, quantity_unit='mL', reagent_cas='64-17-5'"
        in compiled.normalized_python_source
    )

    recompiled = _compile(compiled.normalized_python_source)
    assert recompiled.valid, recompiled.diagnostics
    assert recompiled.graph is not None
    assert recompiled.graph["inventory_requirements"][0]["required_quantity"] == 10.0


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (_source(quantity_arguments="quantity=10"), "必须同时声明"),
        (_source(quantity_arguments="quantity=10, quantity_unit='mL'"), "必须同时声明"),
        (_source(quantity_arguments="quantity=0, quantity_unit='mL', reagent_cas='64-17-5'"), "必须是正数"),
        (_source(quantity_arguments="quantity=-5, quantity_unit='mL', reagent_cas='64-17-5'"), "必须是数字字面量"),
        (_source(quantity_arguments="quantity=True, quantity_unit='mL', reagent_cas='64-17-5'"), "必须是数字字面量"),
        (_source(quantity_arguments="quantity=10, quantity_unit='', reagent_cas='64-17-5'"), "非空"),
        (_source(quantity_arguments="quantity=10, quantity_unit='mL', reagent_cas='64-17-6'"), "校验位无效"),
        (_source(quantity_arguments="quantity=10, quantity_unit='mL', reagent_cas='ethanol'"), "格式无效"),
        (
            _source(role="PRIMARY_SAMPLE", quantity_arguments="quantity=10, quantity_unit='mL', reagent_cas='64-17-5'"),
            "只有试剂",
        ),
    ],
)
def test_invalid_quantity_declarations_are_rejected(source: str, message: str) -> None:
    """数量声明的每条静态规则都必须在解析期拒绝，而不是留到运行时。"""

    with pytest.raises(MaterialAuthoringError) as error:
        parse_authoring_source(
            python_source=source,
            expected_workflow_uuid=WORKFLOW_UUID,
        )
    assert message in str(error.value)


def test_quantity_without_consumer_is_rejected() -> None:
    """声明了数量却没有任何动作消费的来源没有可承担预留的节点，必须拒绝。"""

    compiled = _compile(_source(consumed=False))

    assert not compiled.valid
    assert any(
        "必须被至少一个动作消费" in str(item.get("message", ""))
        for item in compiled.diagnostics
    ), compiled.diagnostics


def test_unknown_cas_is_rejected_at_compile_time() -> None:
    """目录里没有的 CAS 必须让工作流停在草稿无效，而不是留到任务准入。"""

    compiled = _compile(_source(quantity_arguments="quantity=10, quantity_unit='mL', reagent_cas='67-56-1'"))

    assert not compiled.valid
    assert any(
        item.get("code") == "reagent_reference_resolution_error"
        and "67-56-1" in str(item.get("message", ""))
        for item in compiled.diagnostics
    ), compiled.diagnostics


def test_missing_reagent_catalog_port_is_a_stable_diagnostic() -> None:
    """没有试剂目录端口的编译路径不能悄悄生成无身份的需求。"""

    compiled = _compile(_source(), catalog=None)

    assert not compiled.valid
    assert any(
        item.get("code") == "reagent_reference_unavailable" for item in compiled.diagnostics
    ), compiled.diagnostics
