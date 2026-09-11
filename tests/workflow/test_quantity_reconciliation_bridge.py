"""数量结算异常经真实工作流桥、HTTP 更正与重启恢复的合同。"""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from unilabos.app.scheduler.dispatch import CommittedJobOutcome, RecordingDispatcher
from unilabos.app.scheduler.inventory.dispatch_admission import DispatchAdmissionRequest, DispatchResource
from unilabos.app.scheduler.inventory.service import InventoryService
from unilabos.app.scheduler.inventory.workflow_quantity import WorkflowQuantityReservationError
from unilabos.app.scheduler.service import EdgeScheduler
from unilabos.app.workflow_api import create_workflow_app
from unilabos.workflow.physical_settlement_policy import QUANTITY_RECONCILIATION_REQUIRED
from unilabos.workflow.service import WorkflowService
from unilabos.workflow.store import StoreConflict
from unilabos.workflow.task_scheduler_bridge import TaskSchedulerBridge

from .quantity_inventory_test_support import TASK_UUID
from .test_quantity_inventory_reconciliation import _create_task, _scenario


@contextmanager
def _runtime(
    scenario: dict[str, Any],
) -> Iterator[tuple[TaskSchedulerBridge, TestClient, RecordingDispatcher]]:
    """接入真实库存、任务投影和结果桥，记录任何意外设备派发。"""

    dispatcher = RecordingDispatcher()
    scheduler = EdgeScheduler(dispatcher=dispatcher, inventory=InventoryService(scenario["inventory"]))
    bridge = TaskSchedulerBridge(scenario["workflow"], scheduler=scheduler)
    client = TestClient(create_workflow_app(WorkflowService(scenario["workflow"], task_scheduler_bridge=bridge)))
    try:
        yield bridge, client, dispatcher
    finally:
        bridge.close()


def _running(bridge: TaskSchedulerBridge, scenario: dict[str, Any]) -> None:
    """还原已经受理的设备作业；测试从其成功回执进入真实结算链。"""

    for job_uuid in scenario["jobs"]:
        lock_key = f"resource:{job_uuid}"
        authority = bridge._scheduler.station_resource_inventory
        permit = authority.acquire_dispatch_permit(DispatchAdmissionRequest(
            effect_uuid=str(uuid4()), task_uuid=TASK_UUID, job_uuid=job_uuid,
            attempt=1, parameter_hash="quantity-test", expected_change_set={"kind": "no_inventory_change"},
            resources=(DispatchResource(lock_key=lock_key, scope="resource"),),
        )).permit
        assert permit is not None
        bridge._projection.project_pre_dispatch(
            task_uuid=TASK_UUID, job_uuid=job_uuid,
            execution_locks=[{"lock_key": lock_key, "scope": "resource"}],
            dispatch_permit=permit.as_mapping(),
        )
        authority.transition_dispatch_permit(permit.claim_uuid, target_state="reserved")
        authority.transition_dispatch_permit(permit.claim_uuid, target_state="running")
        bridge._projection.project_dispatch_accepted(job_uuid)


def _outcome(
    scenario: dict[str, Any], *, unit: str = "g", quantity: float = 2.0,
    duplicate: bool = False,
) -> CommittedJobOutcome:
    """构造同一设备已提交的成功证明，覆盖错单位与无效重复回执。"""

    item = {"inventory_type": "reagent", "inventory_uuid": scenario["reagent_uuid"],
            "actual_quantity": quantity, "quantity_unit": unit}
    return CommittedJobOutcome(
        outcome="succeeded", return_info={"return_value": {"device": "completed"}},
        error_info=[], unknown_command_ids=[], inventory_consumptions=[item] * (2 if duplicate else 1),
    )


def _correction(scenario: dict[str, Any], *, quantity: float = 2.0) -> dict[str, Any]:
    """显式核对全套实例及单位，不改变已提交的设备执行结果。"""

    return {"reason": "核对泵日志，实际单位为毫升", "consumptions": _outcome(scenario, unit="mL", quantity=quantity).inventory_consumptions}


def _quantity(scenario: dict[str, Any]) -> float:
    return scenario["inventory"].query_one("SELECT quantity FROM reagent WHERE uuid=?", (scenario["reagent_uuid"],))["quantity"]


@pytest.mark.parametrize("duplicate", [False, True])
def test_invalid_success_preserves_reservation_and_public_correction_is_idempotent(
    tmp_path: Path, duplicate: bool,
) -> None:
    """不能对账的成功回执保持运行占用；人工更正仅补记一次库存。"""

    with _scenario(tmp_path) as scenario, _runtime(scenario) as (bridge, client, dispatcher):
        _running(bridge, scenario)
        job_uuid = scenario["jobs"][0]
        with pytest.raises((ValueError, StoreConflict)):
            bridge._on_job_outcome(job_uuid, _outcome(scenario, duplicate=duplicate))
        job = scenario["workflow"].get_job(job_uuid)
        assert job["status"] == "running"
        assert job["uncertainty_reason"] == QUANTITY_RECONCILIATION_REQUIRED
        assert scenario["workflow"].get_task(TASK_UUID)["control_status"] == "waiting_reconciliation"
        assert _quantity(scenario) == 10
        assert {row["state"] for row in bridge._projection.list_execution_locks(job_uuid)} == {"uncertain"}
        assert scenario["inventory"].query_one("SELECT status FROM inventory_reservation")["status"] == "active"
        path = f"/api/v1/workflow-node-jobs/{job_uuid}/reconcile-quantity"
        for _ in range(2):
            response = client.post(path, json=_correction(scenario))
            assert response.status_code == 200, response.text
            assert response.json()["code"] == 0, response.text
        assert _quantity(scenario) == 8
        assert scenario["workflow"].get_job(job_uuid)["status"] == "succeeded"
        assert scenario["workflow"].get_task(TASK_UUID)["status"] == "succeeded"
        assert len(bridge.list_job_inventory_consumptions(job_uuid)) == 1
        assert {row["state"] for row in bridge._projection.list_execution_locks(job_uuid)} == {"released"}
        assert client.post(path, json=_correction(scenario, quantity=1)).json()["code"] != 0
        assert _quantity(scenario) == 8
        assert dispatcher.dispatched == []


def test_restart_keeps_bad_quantity_task_and_recovers_unrelated_task(tmp_path: Path) -> None:
    """坏消费不阻断其他任务的重启清理，也不释放原任务的预留。"""

    with _scenario(tmp_path) as scenario:
        other_task, other_job = str(uuid4()), str(uuid4())
        other_prepared = replace(scenario["prepared"], jobs=[dict(scenario["prepared"].jobs[0], uuid=other_job)])
        _create_task(scenario, other_task, other_prepared)
        with _runtime(scenario) as (bridge, _, _):
            _running(bridge, scenario)
            bridge._replay_persisted_job_outcome(scenario["jobs"][0], _outcome(scenario))
        with _runtime(scenario) as (bridge, client, dispatcher):
            recovered = bridge.recover_active_tasks()
            assert other_task in {item["task"]["uuid"] for item in recovered}
            assert scenario["workflow"].get_task(other_task)["status"] == "failed"
            assert scenario["workflow"].get_job(scenario["jobs"][0])["status"] == "running"
            assert scenario["workflow"].get_task(TASK_UUID)["control_status"] == "waiting_reconciliation"
            response = client.post(f"/api/v1/workflow-node-jobs/{scenario['jobs'][0]}/reconcile-quantity", json=_correction(scenario))
            assert response.json()["code"] == 0, response.text
            assert _quantity(scenario) == 8
            assert dispatcher.dispatched == []


def test_restart_replays_saved_success_after_correction_committed_before_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """跨库更正提交后投影崩溃，可在无 Edge 再次投递时恢复成功事实。"""

    with _scenario(tmp_path) as scenario:
        job_uuid = scenario["jobs"][0]
        with _runtime(scenario) as (bridge, _, _):
            _running(bridge, scenario)
            bridge._replay_persisted_job_outcome(job_uuid, _outcome(scenario))
            def crash(*args, **kwargs):
                raise RuntimeError("模拟库存已提交但工作流投影前退出")
            monkeypatch.setattr(bridge, "_complete_reconciled_quantity_result", crash)
            with pytest.raises(RuntimeError):
                bridge.reconcile_job_quantity(job_uuid, **_correction(scenario))
            assert _quantity(scenario) == 8
        with _runtime(scenario) as (bridge, client, dispatcher):
            bridge.recover_active_tasks()
            assert scenario["workflow"].get_job(job_uuid)["status"] == "succeeded"
            assert scenario["workflow"].get_task(TASK_UUID)["status"] == "succeeded"
            assert _quantity(scenario) == 8
            assert client.post(f"/api/v1/workflow-node-jobs/{job_uuid}/reconcile-quantity", json=_correction(scenario)).json()["code"] == 0
            assert dispatcher.dispatched == []


def test_parallel_success_waits_for_first_correction_then_settles_each_job(tmp_path: Path) -> None:
    """并行结果不能覆盖失败消费意图；更正首个作业后逐个补齐原成功结果。"""

    with _scenario(tmp_path, quantities=(2.0, 2.0)) as scenario, _runtime(scenario) as (bridge, client, dispatcher):
        _running(bridge, scenario)
        first, second = scenario["jobs"]
        bridge._replay_persisted_job_outcome(first, _outcome(scenario))
        bridge._replay_persisted_job_outcome(second, _outcome(scenario, unit="mL"))
        assert _quantity(scenario) == 10
        assert all(scenario["workflow"].get_job(job)["uncertainty_reason"] == QUANTITY_RECONCILIATION_REQUIRED for job in (first, second))
        path = f"/api/v1/workflow-node-jobs/{first}/reconcile-quantity"
        assert client.post(path, json=_correction(scenario)).json()["code"] == 0
        assert _quantity(scenario) == 6
        assert scenario["workflow"].get_task(TASK_UUID)["status"] == "succeeded"
        assert all(scenario["workflow"].get_job(job)["status"] == "succeeded" for job in (first, second))
        assert client.post(path, json=_correction(scenario)).json()["code"] == 0
        assert _quantity(scenario) == 6
        assert dispatcher.dispatched == []


def test_concurrent_corrections_keep_committed_request_and_reject_losing_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """两人更正同一作业时，失败请求不得覆盖审计身份或在重试时伪装成成功。"""

    with _scenario(tmp_path) as scenario, _runtime(scenario) as (bridge, client, dispatcher):
        _running(bridge, scenario)
        job_uuid = scenario["jobs"][0]
        bridge._replay_persisted_job_outcome(job_uuid, _outcome(scenario))
        staged_a, started_b, staged_b, applied_a = Event(), Event(), Event(), Event()
        original_stage = bridge._projection.stage_quantity_reconciliation
        original_reconcile = bridge._quantity_inventory.reconcile_consumption

        def stage(*args, **kwargs):
            quantity = kwargs["consumptions"][0]["actual_quantity"]
            if quantity == 1:
                assert staged_a.wait(5)
            result = original_stage(*args, **kwargs)
            if quantity == 2:
                staged_a.set()
                assert started_b.wait(5)
                # 缺少桥接层串行保护时，让 B 覆盖暂存请求后 A 才落账。
                # 有保护时 B 尚未进入暂存阶段，等待到期后 A 正常完成。
                staged_b.wait(0.25)
            else:
                staged_b.set()
            return result

        def reconcile(*args, **kwargs):
            quantity = kwargs["consumptions"][0]["actual_quantity"]
            if quantity == 1:
                assert applied_a.wait(5)
            result = original_reconcile(*args, **kwargs)
            if quantity == 2:
                applied_a.set()
            return result

        monkeypatch.setattr(bridge._projection, "stage_quantity_reconciliation", stage)
        monkeypatch.setattr(bridge._quantity_inventory, "reconcile_consumption", reconcile)

        def correct(quantity):
            if quantity == 1:
                assert staged_a.wait(5)
                started_b.set()
            try:
                return bridge.reconcile_job_quantity(job_uuid, **_correction(scenario, quantity=quantity))
            except StoreConflict as error:
                return error

        with ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(correct, 2)
            second = workers.submit(correct, 1)
            assert first.result(timeout=10)["status"] == "succeeded"
            assert isinstance(second.result(timeout=10), StoreConflict)
        monkeypatch.setattr(bridge._projection, "stage_quantity_reconciliation", original_stage)
        monkeypatch.setattr(bridge._quantity_inventory, "reconcile_consumption", original_reconcile)

        job = scenario["workflow"].get_job(job_uuid)
        assert job["control_data"]["quantity_reconciliation"]["requested_correction"] == _correction(scenario, quantity=2)
        assert _quantity(scenario) == 8
        path = f"/api/v1/workflow-node-jobs/{job_uuid}/reconcile-quantity"
        assert client.post(path, json=_correction(scenario, quantity=1)).json()["code"] != 0
        assert client.post(path, json=_correction(scenario, quantity=2)).json()["code"] == 0
        assert _quantity(scenario) == 8
        assert len(bridge.list_job_inventory_consumptions(job_uuid)) == 1
        assert dispatcher.dispatched == []


def test_staged_correction_cannot_claim_ordinary_result_consumption_as_its_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """暂存更正不等于已经更正；普通结果先落账后，原暂存请求必须被拒绝。"""

    with _scenario(tmp_path) as scenario, _runtime(scenario) as (bridge, client, dispatcher):
        _running(bridge, scenario)
        job_uuid = scenario["jobs"][0]
        outcome = _outcome(scenario, unit="mL", quantity=2)
        authority = bridge._quantity_inventory._authority
        original_apply = authority.apply_consumption

        def temporarily_rejected(*args, **kwargs):
            raise WorkflowQuantityReservationError("暂时无法读取已核对库存")

        monkeypatch.setattr(authority, "apply_consumption", temporarily_rejected)
        bridge._replay_persisted_job_outcome(job_uuid, outcome)
        assert _quantity(scenario) == 10
        assert scenario["workflow"].get_job(job_uuid)["uncertainty_reason"] == QUANTITY_RECONCILIATION_REQUIRED
        correction = _correction(scenario, quantity=1)
        bridge._projection.stage_quantity_reconciliation(
            job_uuid, **correction, can_replace=True
        )

        # 暂存之后尚未调用人工更正端口，库存恢复让原始 2 mL 成功回执先落账。
        monkeypatch.setattr(authority, "apply_consumption", original_apply)
        bridge._replay_persisted_job_outcome(job_uuid, outcome)
        assert _quantity(scenario) == 8
        assert scenario["workflow"].get_job(job_uuid)["status"] == "succeeded"
        assert scenario["workflow"].get_job(job_uuid)["control_data"]["quantity_reconciliation"]["requested_correction"] == correction

        path = f"/api/v1/workflow-node-jobs/{job_uuid}/reconcile-quantity"
        response = client.post(path, json=correction)
        assert response.status_code == 200
        assert response.json()["code"] != 0
        assert _quantity(scenario) == 8
        assert len(bridge.list_job_inventory_consumptions(job_uuid)) == 1
        assert dispatcher.dispatched == []
