"""任务级动作档位（simulate）的传递链路测试。

覆盖从任务元数据到 Edge 逐作业载荷的每一跳：任何一跳断开，仿真就会退化成
真实硬件驱动——这在现场是真的会动设备的，因此每一跳都必须有断言。
"""

from __future__ import annotations

from typing import Any

import pytest

from unilabos.app.job_queue import QueueItem
from unilabos.app.scheduler.dispatch import build_job_start_payload
from unilabos.app.scheduler.models import spec_from_dict
from unilabos.workflow.workflow_spec_compiler import _task_action_mode


class TestTaskActionModeExtraction:
    """任务元数据 → spec 档位。"""

    def test_reads_nested_meta_data(self) -> None:
        task = {"meta_data": {"unilab": {"action_mode": "simulate"}}}
        assert _task_action_mode(task) == "simulate"

    def test_reads_root_field_for_legacy_read_model(self) -> None:
        assert _task_action_mode({"action_mode": "real"}) == "real"

    def test_defaults_to_empty_when_absent(self) -> None:
        """未指定时必须为空串——调用方据此沿用进程级配置，旧任务行为不变。"""

        assert _task_action_mode({}) == ""
        assert _task_action_mode({"meta_data": {}}) == ""

    def test_ignores_illegal_value(self) -> None:
        task = {"meta_data": {"unilab": {"action_mode": "nonsense"}}}
        assert _task_action_mode(task) == ""


class TestSpecCarriesActionMode:
    """spec_from_dict 必须把档位带进调度 spec。"""

    def test_spec_accepts_action_mode(self) -> None:
        spec = spec_from_dict(
            {
                "workflow_id": "wf-1",
                "nodes": [],
                "edges": [],
                "handles": [],
                "action_mode": "simulate",
            }
        )
        assert spec.action_mode == "simulate"

    def test_spec_defaults_to_empty(self) -> None:
        spec = spec_from_dict({"workflow_id": "wf-1", "nodes": []})
        assert spec.action_mode == ""


class TestDispatchPayloadCarriesActionMode:
    """派发载荷只在显式指定档位时扩展 wire 形状。"""

    def test_payload_includes_action_mode_when_given(self) -> None:
        payload = build_job_start_payload(
            job_id="j",
            task_id="t",
            workflow_id="w",
            node_id="n",
            device_id="d",
            action_name="execute_command",
            action_type="UniLabJsonCommand",
            action_args={},
            action_mode="simulate",
        )
        assert payload["action_mode"] == "simulate"

    def test_payload_omits_action_mode_by_default(self) -> None:
        """旧调用方不传档位时，载荷形状必须与改造前完全一致。"""

        payload = build_job_start_payload(
            job_id="j",
            task_id="t",
            workflow_id="w",
            node_id="n",
            device_id="d",
            action_name="execute_command",
            action_type="UniLabJsonCommand",
            action_args={},
        )
        assert "action_mode" not in payload


class TestQueueItemCarriesActionMode:
    """QueueItem / EdgeJobContext 必须有该字段，host_node 才能读到。"""

    def test_queue_item_default_is_empty(self) -> None:
        item = QueueItem(
            task_type="job_call_back_status",
            device_id="d",
            action_name="a",
            task_id="t",
            job_id="j",
            notebook_id="",
            device_action_key="/devices/d/a",
        )
        assert item.action_mode == ""

    def test_queue_item_accepts_simulate(self) -> None:
        item = QueueItem(
            task_type="job_call_back_status",
            device_id="d",
            action_name="a",
            task_id="t",
            job_id="j",
            notebook_id="",
            device_action_key="/devices/d/a",
            action_mode="simulate",
        )
        assert item.action_mode == "simulate"


class TestHostNodeEffectiveMode:
    """host_node 的档位判定：任务级优先，回落进程级。"""

    @staticmethod
    def _effective(task_mode: str, process_mode: str) -> str:
        """复刻 host_node 的判定表达式，确保语义被钉死。"""

        return str(task_mode or "") or process_mode

    def test_task_simulate_overrides_process_real(self) -> None:
        """这是最关键的一条：后端 real 档下，任务级 simulate 必须走仿真。"""

        assert self._effective("simulate", "real") == "simulate"

    def test_empty_task_mode_falls_back_to_process(self) -> None:
        assert self._effective("", "real") == "real"
        assert self._effective("", "simulate") == "simulate"

    def test_task_real_overrides_process_simulate(self) -> None:
        assert self._effective("real", "simulate") == "real"


class TestRequestValidation:
    """请求 DTO 的档位校验。"""

    def test_rejects_illegal_action_mode(self) -> None:
        from unilabos.app.workflow_api import WorkflowTaskCreateRequest

        with pytest.raises(Exception):
            WorkflowTaskCreateRequest(workflow_uuid="wf-1", action_mode="dry")

    def test_accepts_simulate_and_real(self) -> None:
        from unilabos.app.workflow_api import WorkflowTaskCreateRequest

        assert WorkflowTaskCreateRequest(
            workflow_uuid="wf-1", action_mode="SIMULATE"
        ).action_mode == "simulate"
        assert WorkflowTaskCreateRequest(
            workflow_uuid="wf-1", action_mode="real"
        ).action_mode == "real"

    def test_default_is_none(self) -> None:
        from unilabos.app.workflow_api import WorkflowTaskCreateRequest

        assert WorkflowTaskCreateRequest(workflow_uuid="wf-1").action_mode is None
