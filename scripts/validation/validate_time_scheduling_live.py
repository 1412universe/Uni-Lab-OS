"""只读预检，或显式运行两条纯软件探针 Task；不操作 PLC、库存或集群。"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from uuid import uuid4


WORKFLOW_UUID = "afbf7e17-a53c-575c-9a74-810ef3d887e2"
DEVICE_ID = "szlab_control_flow_probe"
ACTIONS = {"begin_error_scenario", "error_checkpoint", "finish_error_scenario"}
TERMINAL = {"succeeded", "failed", "canceled", "timeout"}


class LiveValidation:
    """保存每次请求和已创建 Task；创建请求不重试，避免受理不明时重复任务。"""

    def __init__(self, base_url: str, output: Path) -> None:
        self.base_url = base_url.rstrip("/")
        self.output = output
        self.report: dict[str, Any] = {
            "status": "running", "run_id": str(uuid4()), "base_url": self.base_url,
            "task_ids": [], "requests": [], "plan_observations": [],
            "scope": "纯软件 Edge 动作、实际成功与锁释放；不证明硬时限或 PLC 连续区间",
        }

    def save(self) -> None:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.output.write_text(json.dumps(self.report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def request(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        record: dict[str, Any] = {"method": "GET" if payload is None else "POST", "path": path}
        if payload is not None:
            record["payload"] = payload
        self.report["requests"].append(record)
        self.save()
        data = None if payload is None else json.dumps(payload).encode()
        request = Request(self.base_url + path, data=data, headers={"Content-Type": "application/json"})
        # 不自动重发 POST；请求超时时以记录中的 run_id/描述在任务列表核实受理结果。
        with urlopen(request, timeout=15) as response:
            body = json.load(response)
            record["http_status"] = response.status
        if isinstance(body, dict) and "code" in body:
            if body["code"] != 0:
                raise RuntimeError(f"接口拒绝 {path}: {body}")
            body = body.get("data")
        record["response"] = body
        self.save()
        return body

    def preflight(self) -> None:
        scheduler = self.request("/api/v1/readiness")
        if scheduler.get("status") != "ready" or scheduler.get("startupMode") != "product":
            raise RuntimeError("需要就绪且 product 模式的 Scheduler；脚本不自动切模式")
        edge = self.request("/api/v1/edge/readiness")
        if edge.get("status") != "ready":
            raise RuntimeError("Edge Runtime 尚未就绪")
        self.observe_plan()
        station = self.request("/api/v1/stations/local/error-handling")
        if station.get("mode") != "RUNNING" or station.get("errors") or station.get("active_session_id"):
            raise RuntimeError("存在工站暂停/未结处置；脚本不自动恢复或结案")
        graph = self.request(f"/api/v1/workflows/{WORKFLOW_UUID}/graph")
        nodes = graph.get("nodes", [])
        if len(nodes) != 3 or {node.get("action_name") for node in nodes} != ACTIONS:
            raise RuntimeError("软件探针工作流已改变，不能按固定验收合同启动")
        for node in nodes:
            binding = node.get("meta_data", {}).get("unilab", {}).get("executor_binding", {})
            if binding != {"device_id": DEVICE_ID, "mode": "fixed"} or node.get("disabled"):
                raise RuntimeError("探针节点不是已核对的固定软件设备")
        resources = self.request("/api/v1/resources")
        matches = [item for item in resources if item.get("id") == DEVICE_ID]
        if len(matches) != 1 or matches[0].get("class") != "community.szlab_poly_studio.szlab_control_flow_probe":
            raise RuntimeError("在线资源目录未确认软件探针类型，拒绝自动创建任务")
        self.report["preflight_passed"] = True
        self.report["driver_boundary"] = "在线资源身份与已审核纯软件类匹配；不是远端源码哈希证明"

    def observe_plan(self) -> None:
        plan = self.request("/api/v1/scheduler/time-plan")
        if plan.get("enabled") is not True or plan.get("algorithm") != "CP-SAT":
            raise RuntimeError("当前服务未启用 CP-SAT 时间排程")
        if plan.get("guaranteed_hard_time_limits") is not False:
            raise RuntimeError("计划接口缺少明确的硬时限覆盖边界")
        latest = plan.get("latest")
        if latest and latest.get("status") in {"FEASIBLE", "OPTIMAL"} and not latest.get("validated"):
            raise RuntimeError("求解候选未通过独立验证")
        self.report["plan_observations"].append(plan)

    def execute(self, timeout_seconds: float) -> None:
        for label in ("A", "B"):
            task = self.request("/api/v1/workflow-tasks", {
                "workflow_uuid": WORKFLOW_UUID, "run_mode": "normal", "priority": "normal",
                "input": {"failure_count": 0, "delay_seconds": 2},
                "description": f"时间排程纯软件验收 {self.report['run_id']} {label}",
                "meta_data": {"validation_run_id": self.report["run_id"], "simulation_only": True},
            })
            self.report["task_ids"].append(task["uuid"])
            self.save()
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            tasks = [self.request(f"/api/v1/workflow-tasks/{identity}") for identity in self.report["task_ids"]]
            self.observe_plan()
            if any(task["status"] in TERMINAL - {"succeeded"} for task in tasks):
                raise RuntimeError("探针任务未成功；已保留 Task 身份，不自动取消/重试/释放")
            if all(task["status"] == "succeeded" for task in tasks):
                self.verify_completed()
                return
            time.sleep(0.5)
        raise TimeoutError("验收等待超时；保留任务和占用供检查，未自动停止或释放")

    def verify_completed(self) -> None:
        intervals: list[tuple[datetime, datetime, str]] = []
        completed: dict[str, Any] = {}
        for identity in self.report["task_ids"]:
            jobs = self.request(f"/api/v1/workflow-tasks/{identity}/jobs")
            device_jobs = [job for job in jobs if job.get("executor_kind") == "device_action"]
            if len(device_jobs) != 3 or any(job["status"] != "succeeded" for job in jobs):
                raise RuntimeError("每个软件任务应恰好有三个真实成功的节点尝试")
            # 输入、输出等纯数据节点也出现在 Job 列表，但没有设备实际起止事件。
            actions = set()
            for job in device_jobs:
                payload = job.get("control_data", {}).get("dispatch_payload", {})
                if payload.get("device_id") != DEVICE_ID:
                    raise RuntimeError("实际派发设备不属于已核对的软件探针")
                actions.add(payload.get("action"))
                if job.get("uncertainty_reason") or not job.get("started_at") or not job.get("finished_at"):
                    raise RuntimeError("成功节点缺少实际起止证据或仍有不确定性")
                start = datetime.fromisoformat(job["started_at"].replace("Z", "+00:00"))
                finish = datetime.fromisoformat(job["finished_at"].replace("Z", "+00:00"))
                if finish < start:
                    raise RuntimeError("实际事件时间顺序无效")
                intervals.append((start, finish, job["uuid"]))
            if actions != ACTIONS:
                raise RuntimeError("实际完成动作不符合三个软件探针动作合同")
            locks = self.request(f"/api/v1/workflow-tasks/{identity}/execution-locks")
            if locks.get("locks") or locks.get("active_device_tenancy_count") != 0:
                raise RuntimeError("实际成功后仍有未释放的动作占用或设备托管")
            events = self.request(f"/api/v1/workflow-tasks/{identity}/events?limit=200")
            completed[identity] = {"jobs": jobs, "execution_locks": locks, "events": events}
        ordered = sorted(intervals)
        if any(right[0] < left[1] for left, right in zip(ordered, ordered[1:])):
            raise RuntimeError("同一软件设备的动作实际执行窗口发生重叠")
        self.report["completed"] = completed
        self.report["actual_intervals_non_overlapping"] = True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true", help="显式创建两个纯软件任务；默认只读")
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    args = parser.parse_args()
    validation = LiveValidation(args.base_url, args.output)
    try:
        validation.preflight()
        if args.execute:
            validation.execute(args.timeout_seconds)
        validation.report["status"] = "passed" if args.execute else "preflight_passed"
    except Exception as error:
        validation.report["status"] = "failed"
        validation.report["error"] = str(error)
        raise
    finally:
        validation.save()
        print(json.dumps({key: validation.report[key] for key in ("status", "run_id", "task_ids")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
