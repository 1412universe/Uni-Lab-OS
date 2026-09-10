"""默认单进程执行器在真实调用边界遵守工站暂停与原命令恢复。"""

import threading

from tests.scheduler_core.conftest import build_core_runtime
from unilabos.app.edge_control.execution_start import confirm_execution_start
from unilabos.app.scheduler.backend import JobExecutionBackend


class RecordingHost:
    def __init__(self):
        self.calls = []
        self.called = threading.Event()
        self.before_start = lambda _item: None

    def send_goal(self, item, **kwargs):
        self.before_start(item)
        confirm_execution_start(item)
        self.calls.append(item)
        self.called.set()


def test_queued_action_waits_for_human_resume_and_starts_once(tmp_path):
    host = RecordingHost()
    backend = JobExecutionBackend(host_node_getter=lambda: host)
    runtime = build_core_runtime(tmp_path, execution_backend=backend)
    try:
        source = runtime.submit(task_name="source-error", devices=["reactor-a"])
        waiting = runtime.submit(task_name="queued-action", devices=["reactor-b"])
        source_job = source["jobs"][0]["uuid"]
        waiting_job = waiting["jobs"][0]["uuid"]
        control = runtime.bridge.error_control
        control.report_error(source_job, phase="execution", message="设备错误")
        backend.start()
        assert backend.wait_idle()
        assert host.calls == []
        assert (
            runtime.workflow_store.get_job(waiting_job)["start_state"] == "NOT_STARTED"
        )

        snapshot = control.snapshot()
        control.resume(
            {
                "expected_station_version": snapshot["station_version"],
                "observed_error_epoch": snapshot["error_epoch"],
                "operator_confirmed": True,
                "confirm_snapshot_id": snapshot["snapshot_id"],
            },
            idempotency_key="human-resume",
            operator_id="tester",
        )
        runtime.bridge.reschedule()
        assert host.called.wait(2)
        assert [item.job_id for item in host.calls] == [waiting_job]
        assert (
            runtime.workflow_store.get_job(waiting_job)["start_state"]
            == "START_COMMITTED"
        )
        runtime.scheduler.restore_not_started_dispatches()
        assert backend.wait_idle()
        assert [item.job_id for item in host.calls] == [waiting_job]
    finally:
        backend.stop()
        runtime.close()


def test_pause_between_host_delivery_and_driver_call_keeps_running_results(tmp_path):
    """队列检查之后的新错误仍挡住动作；已开始动作的真实结果继续结算。"""
    host = RecordingHost()
    backend = JobExecutionBackend(host_node_getter=lambda: host)
    runtime = build_core_runtime(tmp_path, execution_backend=backend)
    backend.add_job_finished_listener(runtime.scheduler.on_job_finished)
    try:
        first = runtime.submit(task_name="running", devices=["reactor-a"])["jobs"][0][
            "uuid"
        ]
        waiting = runtime.submit(task_name="waiting", devices=["reactor-b"])["jobs"][0][
            "uuid"
        ]
        control = runtime.bridge.error_control

        def pause_before_driver(item):
            if item.job_id == waiting:
                control.report_error(first, phase="execution", message="执行期间故障")

        host.before_start = pause_before_driver
        backend.start()
        assert backend.wait_idle()
        assert [item.job_id for item in host.calls] == [first]
        assert runtime.workflow_store.get_job(waiting)["start_state"] == "NOT_STARTED"
        backend.publish_job_status(
            {}, host.calls[0], "success", {"suc": True, "return_value": {"done": True}}
        )
        assert backend.wait_idle()
        assert runtime.workflow_store.get_job(first)["status"] == "succeeded"
        assert control.snapshot()["mode"] == "PAUSED"
        assert [item.job_id for item in host.calls] == [first]
    finally:
        backend.stop()
        runtime.close()


def test_cancel_before_worker_starts_never_reaches_device(tmp_path):
    """撤销已排队但未启动的动作必须有明确未发送证明。"""
    host = RecordingHost()
    backend = JobExecutionBackend(host_node_getter=lambda: host)
    runtime = build_core_runtime(tmp_path, execution_backend=backend)
    try:
        aggregate = runtime.submit(task_name="cancel-queued", devices=["reactor-a"])
        runtime.bridge.cancel(aggregate["task"]["uuid"])
        backend.start()
        assert backend.wait_idle()
        assert host.calls == []
        assert (
            runtime.workflow_store.get_job(aggregate["jobs"][0]["uuid"])["status"]
            == "canceled"
        )
    finally:
        backend.stop()
        runtime.close()
