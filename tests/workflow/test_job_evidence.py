"""工作流作业反馈与不可变结果证据的合同测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from unilabos.app.workflow_api import create_workflow_app
from unilabos.workflow.service import WorkflowService
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
