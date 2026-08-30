"""工作流作业反馈与不可变结果证据的合同测试。"""

from __future__ import annotations

from threading import Event

from fastapi.testclient import TestClient
import pytest

from unilabos.app.workflow_api import create_workflow_app
from unilabos.workflow.service import WorkflowService
from unilabos.workflow.station_event_outbox import (
    StationEventOutboxStore,
    append_station_event,
)
from unilabos.workflow.station_event_publisher import StationEventPublisher
from unilabos.workflow.store import StoreConflict, WorkflowStore
from unilabos.workflow.task_runtime_projection import TaskRuntimeProjection

WORKFLOW_UUID = "10000000-0000-4000-8000-000000000101"
TASK_UUID = "20000000-0000-4000-8000-000000000101"
NODE_UUID = "30000000-0000-4000-8000-000000000101"
JOB_UUID = "40000000-0000-4000-8000-000000000101"
CREATED_AT = "2026-08-26T00:00:00Z"


def _seed_job(store: WorkflowStore) -> None:
    """创建一个可以进入派发阶段的标准任务与作业。"""

    store.create_workflow(
        workflow_uuid=WORKFLOW_UUID,
        name="反馈证据测试",
        tags=[],
        description=None,
        meta_data={},
    )
    with store.transaction() as connection:
        connection.execute(
            """
            INSERT INTO workflow_task(
                uuid, create_time, update_time, deleted_at, description,
                meta_data, workflow_uuid, status, workflow_snapshot,
                execution_plan, run_mode, target_node_uuid, control_status,
                cleanup_status, trace_context, input, output, error_info
            ) VALUES (?, ?, ?, NULL, NULL, '{}', ?, 'pending', '{}', '{}',
                      'normal', NULL, 'active', 'none', '{}', '{}', '{}', '[]')
            """,
            (TASK_UUID, CREATED_AT, CREATED_AT, WORKFLOW_UUID),
        )
        connection.execute(
            """
            INSERT INTO workflow_node_job(
                uuid, create_time, update_time, deleted_at, description,
                meta_data, workflow_task_uuid, workflow_node_uuid,
                feedback_sequence, topological_index, executor_kind,
                execution_policy, execution_timeout_seconds, status,
                attempt, param, feedback_data, return_info, control_data,
                error_info
            ) VALUES (?, ?, ?, NULL, NULL, '{}', ?, ?, 0, 0,
                      'device_action', '{}', 0, 'pending', 1, '{}', '{}',
                      '{}', '{}', '[]')
            """,
            (JOB_UUID, CREATED_AT, CREATED_AT, TASK_UUID, NODE_UUID),
        )


def test_feedback_is_ordered_idempotent_and_queryable(tmp_path) -> None:
    """反馈须先持久化、可重放，并通过公共 HTTP 接口分页读取。"""

    store = WorkflowStore(tmp_path / "job_evidence.db")
    try:
        _seed_job(store)
        projection = TaskRuntimeProjection(store)
        projection.project_pre_dispatch(task_uuid=TASK_UUID, job_uuid=JOB_UUID)

        first = projection.project_feedback(
            job_uuid=JOB_UUID,
            sequence=1,
            feedback_type="progress",
            data={"percent": 20},
            observed_at="2026-08-26T00:00:01Z",
            idempotency_key="feedback-1",
        )
        replay = projection.project_feedback(
            job_uuid=JOB_UUID,
            sequence=1,
            feedback_type="progress",
            data={"percent": 20},
            observed_at="2026-08-26T00:00:01Z",
            idempotency_key="feedback-1",
        )
        projection.project_feedback(
            job_uuid=JOB_UUID,
            sequence=3,
            feedback_type="temperature",
            data={"celsius": 37.0},
            observed_at="2026-08-26T00:00:03Z",
            idempotency_key="feedback-3",
        )

        assert first == {"through_sequence": 1, "created": 1}
        assert replay == {"through_sequence": 1, "created": 0}
        client = TestClient(create_workflow_app(WorkflowService(store)))
        page = client.get(
            f"/api/v1/workflow-node-jobs/{JOB_UUID}/feedback",
            params={"page": 1, "page_size": 1},
        ).json()["data"]
        assert page["page"] == 1
        assert page["page_size"] == 1
        assert page["has_more"] is True
        assert page["items"][0]["sequence"] == 1
        assert page["items"][0]["data"] == {"percent": 20}

        with pytest.raises(StoreConflict, match="内容冲突"):
            projection.project_feedback(
                job_uuid=JOB_UUID,
                sequence=1,
                feedback_type="progress",
                data={"percent": 30},
                observed_at="2026-08-26T00:00:01Z",
                idempotency_key="feedback-1",
            )
    finally:
        store.close()


def test_terminal_result_is_immutable_and_feedback_closes(tmp_path) -> None:
    """明确作业结果只冻结一次，终态之后拒绝新增过程反馈。"""

    store = WorkflowStore(tmp_path / "job_result.db")
    try:
        _seed_job(store)
        projection = TaskRuntimeProjection(store)
        projection.project_pre_dispatch(task_uuid=TASK_UUID, job_uuid=JOB_UUID)
        projection.project_feedback(
            job_uuid=JOB_UUID,
            sequence=1,
            feedback_type="progress",
            data={"percent": 100},
            observed_at="2026-08-26T00:00:01Z",
            idempotency_key="feedback-1",
        )
        projection.project_job_finished(
            job_uuid=JOB_UUID,
            scheduler_state="success",
            return_info={"message": "done"},
        )
        projection.project_job_finished(
            job_uuid=JOB_UUID,
            scheduler_state="success",
            return_info={"message": "done"},
        )

        with store.transaction() as connection:
            result = connection.execute(
                """
                SELECT outcome, return_info FROM workflow_node_job_result
                WHERE workflow_node_job_uuid = ?
                """,
                (JOB_UUID,),
            ).fetchone()
            count = connection.execute(
                """
                SELECT COUNT(*) FROM workflow_node_job_result
                WHERE workflow_node_job_uuid = ?
                """,
                (JOB_UUID,),
            ).fetchone()[0]
        assert tuple(result) == ("succeeded", '{"message":"done"}')
        assert count == 1

        with pytest.raises(StoreConflict, match="终态"):
            projection.project_feedback(
                job_uuid=JOB_UUID,
                sequence=2,
                feedback_type="late",
                data={},
                observed_at="2026-08-26T00:00:02Z",
                idempotency_key="feedback-2",
            )
    finally:
        store.close()


def test_actual_params_feedback_and_result_share_durable_station_outbox(tmp_path) -> None:
    """实参、反馈与结果先在工站库提交，Backend ACK 前可反复重放。

    参数：``tmp_path`` 提供隔离 SQLite 路径。返回无；断言事件序列、幂等身份和
    ACK 结算。异常：本地事实与发件箱不是同一事务或 ACK 错标会使测试失败。
    """

    store = WorkflowStore(tmp_path / "station-outbox.db")
    try:
        _seed_job(store)
        projection = TaskRuntimeProjection(store)
        actual_param = {
            "device_uuid": "50000000-0000-4000-8000-000000000101",
            "target_site_uuid": "60000000-0000-4000-8000-000000000101",
        }
        projection.project_pre_dispatch(
            task_uuid=TASK_UUID,
            job_uuid=JOB_UUID,
            resolved_param=actual_param,
            execution_locks=[
                {"lock_key": "/devices/reactor-a", "scope": "device"},
            ],
        )
        projection.project_feedback(
            job_uuid=JOB_UUID,
            sequence=1,
            feedback_type="progress",
            data={"percent": 60},
            observed_at="2026-08-26T00:00:01Z",
            idempotency_key="feedback-1",
        )
        projection.project_feedback(
            job_uuid=JOB_UUID,
            sequence=1,
            feedback_type="progress",
            data={"percent": 60},
            observed_at="2026-08-26T00:00:01Z",
            idempotency_key="feedback-1",
        )
        projection.project_job_finished(
            job_uuid=JOB_UUID,
            scheduler_state="success",
            return_info={"mass_g": 12.5},
        )
        projection.project_job_finished(
            job_uuid=JOB_UUID,
            scheduler_state="success",
            return_info={"mass_g": 12.5},
        )

        outbox = StationEventOutboxStore(store)
        events = outbox.list_pending()
        assert [event["station_sequence"] for event in events] == [1, 2, 3, 4, 5]
        assert [event["event_type"] for event in events] == [
            "task.running",
            "job.dispatched",
            "job.feedback_committed",
            "job.outcome_committed",
            "task.succeeded",
        ]
        assert events[1]["payload"]["actual_param"] == actual_param
        assert events[3]["payload"]["actual_param"] == actual_param
        assert events[3]["payload"]["return_info"] == {"mass_g": 12.5}

        attempted = outbox.mark_delivery_attempt(
            event_uuid=events[2]["event_uuid"],
            station_sequence=events[2]["station_sequence"],
        )
        assert attempted["delivery_attempts"] == 1
        assert attempted["acked_at"] is None
        assert len(outbox.list_pending()) == 5

        outbox.acknowledge(
            event_uuid=events[2]["event_uuid"],
            station_sequence=events[2]["station_sequence"],
        )
        outbox.acknowledge(
            event_uuid=events[3]["event_uuid"],
            station_sequence=events[3]["station_sequence"],
        )
        remaining = outbox.list_pending()
        assert [event["event_type"] for event in remaining] == [
            "task.running",
            "job.dispatched",
            "task.succeeded",
        ]
        with store.transaction() as connection:
            feedback_published_at = connection.execute(
                """
                SELECT published_at FROM workflow_node_job_feedback_history
                WHERE workflow_node_job_uuid = ? AND sequence = 1
                """,
                (JOB_UUID,),
            ).fetchone()[0]
            result_consumed_at = connection.execute(
                """
                SELECT consumed_at FROM workflow_node_job_result
                WHERE workflow_node_job_uuid = ?
                """,
                (JOB_UUID,),
            ).fetchone()[0]
        assert feedback_published_at is not None
        assert result_consumed_at is not None
    finally:
        store.close()


def test_station_event_publisher_retries_and_accepts_only_exact_prefix(tmp_path) -> None:
    """Backend 断线保留事件，恢复后按精确连续前缀 ACK。

    参数：``tmp_path`` 提供隔离工作流库。返回无；断言发送异常、部分 ACK 和后续
    重放均不跳过序列。异常：发布器错误清理未 ACK 事件会使测试失败。
    """

    store = WorkflowStore(tmp_path / "station-publisher.db")
    try:
        outbox = StationEventOutboxStore(store)
        with store.transaction() as connection:
            for index in (1, 2):
                append_station_event(
                    connection,
                    event_type="job.feedback_committed",
                    aggregate_type="workflow_node_job",
                    aggregate_uuid=JOB_UUID,
                    source_kind="test",
                    source_uuid=f"source-{index}",
                    idempotency_key=f"publisher-test-{index}",
                    payload={"index": index},
                )

        calls = 0

        def sender(events):
            """首次模拟 Backend 断线，之后每次只确认当前批次第一条。"""

            nonlocal calls
            calls += 1
            if calls == 1:
                raise ConnectionError("backend disconnected")
            return [
                {
                    "event_uuid": events[0]["event_uuid"],
                    "station_sequence": events[0]["station_sequence"],
                }
            ]

        publisher = StationEventPublisher(outbox, sender)
        with pytest.raises(ConnectionError, match="disconnected"):
            publisher.flush_once()
        after_failure = outbox.list_pending()
        assert len(after_failure) == 2
        assert [event["delivery_attempts"] for event in after_failure] == [1, 1]

        assert publisher.flush_once() == 1
        remaining = outbox.list_pending()
        assert [event["station_sequence"] for event in remaining] == [2]
        assert publisher.flush_once() == 1
        assert outbox.list_pending() == []
    finally:
        store.close()


def test_station_event_publisher_stop_never_returns_while_sender_uses_store(
    tmp_path,
) -> None:
    """停止发布器必须在存储关闭前证明后台线程已经退出。

    参数：``tmp_path`` 提供隔离工作流库。返回：无；断言显式停止预算不足时抛出
    ``TimeoutError`` 而不是假装已停止，发送结束后重试可以正常退出。异常：若
    发布线程仍可能在 ``stop`` 正常返回后访问 SQLite，本测试失败。
    """

    store = WorkflowStore(tmp_path / "station-publisher-stop.db")
    outbox = StationEventOutboxStore(store)
    sender_started = Event()
    sender_may_finish = Event()
    try:
        with store.transaction() as connection:
            append_station_event(
                connection,
                event_type="job.feedback_committed",
                aggregate_type="workflow_node_job",
                aggregate_uuid=JOB_UUID,
                source_kind="test",
                source_uuid="blocking-sender",
                idempotency_key="publisher-stop-race",
                payload={"sequence": 1},
            )

        def blocking_sender(events):
            """模拟仍处于有限 HTTP 请求中的 Backend 适配器。

            参数：``events`` 是当前按序投递批次。返回：释放测试门闩后确认首个
            事件。异常：等待超过测试预算时抛 ``AssertionError``，避免测试挂起。
            """

            sender_started.set()
            assert sender_may_finish.wait(timeout=2.0)
            return [
                {
                    "event_uuid": events[0]["event_uuid"],
                    "station_sequence": events[0]["station_sequence"],
                }
            ]

        publisher = StationEventPublisher(outbox, blocking_sender, poll_interval=0.01)
        publisher.start()
        assert sender_started.wait(timeout=1.0)
        with pytest.raises(TimeoutError, match="未在停止预算内退出"):
            publisher.stop(timeout=0.01)
        sender_may_finish.set()
        publisher.stop(timeout=1.0)
    finally:
        sender_may_finish.set()
        store.close()
