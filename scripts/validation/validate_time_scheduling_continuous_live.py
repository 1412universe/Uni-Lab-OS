"""连续持锁 A/B 的部署后验证：默认只读，--execute 才创建两条 PLC-Sim 任务。

只接受已经核对的三个模拟设备、S06/S07 通道和 plc-sim 端点。失败或超时只保存
证据，不取消、不重试任务、不切模式、不释放锁。此脚本不直接发送 OPC-UA 命令。
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time
from typing import Any
from urllib.request import Request, urlopen
from uuid import uuid4


DEVICE_ACTIONS = (
    ("continuous_lock_source_rotator", "rotate_to_pick_position"),
    ("continuous_lock_target_rotator", "rotate_to_place_position"),
    ("continuous_lock_robot", "transfer_material"),
)
EXPECTED_CONFIGS = {
    "continuous_lock_source_rotator": {"channel": "S06", "endpoint": "opc.tcp://plc-sim:4855/xuse_sim/"},
    "continuous_lock_target_rotator": {"channel": "S07", "endpoint": "opc.tcp://plc-sim:4855/xuse_sim/"},
    "continuous_lock_robot": {},
}
WORKFLOWS = {
    "A": ("70000000-0000-4000-8000-000000000001", (
        "d01597f8-c8d8-5fcf-b187-c20778493a78", "d970ea2e-124d-5225-9fbc-87dedfdcbca4", "2ffc4c75-3fb7-5daf-b60a-4f44d0a5cc1b")),
    "B": ("70000000-0000-4000-8000-000000000002", (
        "a7062948-8b60-5fe4-84ac-3d056190b4fe", "1e6eb5aa-fb42-57dc-ae60-532c367ce517", "aa314c13-2408-59dd-8a49-1ebc34716185")),
}
TERMINAL = {"succeeded", "failed", "canceled", "timeout"}


def timestamp(value: Any) -> datetime:
    """只接受带时区的实际 Job 时间，避免跨时区或缺失证据被默认为零。"""
    if not isinstance(value, str) or not value:
        raise RuntimeError("Job 缺少实际起止时间")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise RuntimeError("Job 时间没有时区，不能比较实际执行顺序")
    return result.astimezone(timezone.utc)


class ContinuousLiveValidation:
    """逐次保存请求与已受理任务；POST 结果不明时绝不自动重发。"""

    def __init__(self, base_url: str, output: Path, hold_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.output = output
        self.hold_seconds = hold_seconds
        self.report: dict[str, Any] = {
            "status": "running", "run_id": str(uuid4()), "base_url": self.base_url,
            "hold_seconds": hold_seconds, "task_ids": [], "task_labels": {},
            "requests": [], "plan_observations": [], "run_observations": [],
            "scope": "仅 PLC-Sim S06/S07 与软件机械臂；验证实际 Job 观测区间与锁释放，不证明物理硬时限",
        }

    def save(self) -> None:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.output.with_name(self.output.name + ".tmp")
        temporary.write_text(json.dumps(self.report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.output)

    def request(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        record: dict[str, Any] = {"method": "GET" if payload is None else "POST", "path": path,
                                  "requested_at": datetime.now(timezone.utc).isoformat()}
        if payload is not None:
            record["payload"] = payload
        self.report["requests"].append(record)
        self.save()
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(self.base_url + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=15) as response:
                body = json.load(response)
                record["http_status"] = response.status
            record["response"] = body
            self.save()
            if isinstance(body, dict) and "code" in body:
                if body["code"] != 0:
                    raise RuntimeError(f"接口拒绝 {path}: {body}")
                body = body.get("data")
            return body
        except Exception as error:
            record["error"] = str(error)
            if payload is not None:
                record["post_not_retried"] = True
            self.save()
            raise

    def check_resources(self) -> None:
        resources = self.request("/api/v1/resources")
        if not isinstance(resources, list):
            raise RuntimeError("在线资源目录形状未知，拒绝提交")
        confirmed = []
        for identity, expected_config in EXPECTED_CONFIGS.items():
            matches = [resource for resource in resources if resource.get("id") == identity]
            if len(matches) != 1:
                raise RuntimeError(f"模拟资源身份不是唯一在线对象：{identity}")
            resource = matches[0]
            if (resource.get("class") != "community.szlab_poly_studio." + identity
                    or resource.get("type") != "device" or resource.get("config") != expected_config):
                raise RuntimeError(f"在线模拟类、设备类型或通道/端点配置不严格匹配：{identity}")
            confirmed.append(resource)
        self.report["confirmed_resources"] = confirmed
        self.report["driver_boundary"] = "在线注册身份和精确配置与已审源码一致；不声称已校验远端驱动文件哈希"

    def check_no_foreign_active_tasks(self) -> None:
        allowed = set(self.report["task_ids"])
        # 同时排除未结清理的旧终态任务，不能仅凭业务失败判断设备已经空闲。
        filters = [f"status={status}" for status in ("pending", "running", "canceling")]
        filters += [f"cleanup_status={status}" for status in ("pending", "canceling", "requires_attention")]
        for query in filters:
            result = self.request("/api/v1/workflow-tasks?page=1&page_size=100&" + query)
            items, total = result.get("items"), result.get("total")
            if (not isinstance(items, list) or not isinstance(total, int) or isinstance(total, bool)
                    or total < 0 or total != len(items)):
                raise RuntimeError("活动任务列表不完整或形状未知，拒绝提交更多任务")
            foreign = [task for task in items if task.get("uuid") not in allowed]
            if foreign:
                self.report["foreign_active_tasks"] = foreign
                raise RuntimeError("已有其他活动任务或未结清理；请核实，脚本不自动取消或释放")

    def check_graph(self, label: str) -> dict[str, Any]:
        workflow_uuid, node_ids = WORKFLOWS[label]
        graph = self.request(f"/api/v1/workflows/{workflow_uuid}/graph")
        nodes = graph.get("nodes", [])
        if len(nodes) != 3 or {node.get("uuid") for node in nodes} != set(node_ids):
            raise RuntimeError(f"{label} 工作流的三个设备节点身份已变化")
        nodes_by_id = {node["uuid"]: node for node in nodes}
        for identity, (device_id, action) in zip(node_ids, DEVICE_ACTIONS):
            node = nodes_by_id[identity]
            binding = node.get("meta_data", {}).get("unilab", {}).get("executor_binding", {})
            if (binding != {"device_id": device_id, "mode": "fixed"}
                    or node.get("action_name") != action or node.get("disabled")):
                raise RuntimeError(f"{label} 节点不是已核对的固定模拟动作：{identity}")
        edges = {(edge.get("source_node_uuid"), edge.get("target_node_uuid")) for edge in graph.get("edges", [])}
        if edges != set(zip(node_ids, node_ids[1:])):
            raise RuntimeError(f"{label} 不再是来源、目标、机械臂的严格串行流程")
        metadata = graph.get("workflow", {}).get("meta_data", {}).get("unilab", {})
        scopes = metadata.get("resource_scopes", [])
        if len(scopes) != 1:
            raise RuntimeError(f"{label} 缺少唯一的连续资源作用域")
        scope = scopes[0]
        if (scope.get("kind") != "with" or scope.get("hard_boundary") is not True
                or set(scope.get("resources", [])) != set(EXPECTED_CONFIGS)
                or tuple(scope.get("node_uuids", [])) != node_ids
                or scope.get("entry_node_uuid") != node_ids[0]
                or scope.get("exit_node_uuid") != node_ids[-1]):
            raise RuntimeError(f"{label} 连续持锁范围已变化")
        parameters = metadata.get("input_contract", {}).get("parameters", [])
        if len(parameters) != 1 or parameters[0].get("name") != "hold_seconds":
            raise RuntimeError(f"{label} 输入合同不是唯一 hold_seconds 参数")
        return graph

    def observe_plan(self) -> dict[str, Any]:
        plan = self.request("/api/v1/scheduler/time-plan")
        if plan.get("enabled") is not True or plan.get("algorithm") != "CP-SAT":
            raise RuntimeError("未启用 CP-SAT 时间排程")
        if plan.get("guaranteed_hard_time_limits") is not False:
            raise RuntimeError("计划接口没有明确声明硬时限能力范围")
        latest = plan.get("latest")
        if latest and latest.get("status") in {"OPTIMAL", "FEASIBLE"} and not latest.get("validated"):
            raise RuntimeError("候选计划未通过独立验证")
        # 动态参数依赖会暂时留下未知尾部；记录其出现，不把它等同任务失败。
        own_tasks = set(self.report["task_ids"])
        partial = set(plan.get("input", {}).get("partial_tasks", []))
        if partial & own_tasks:
            self.report["unknown_tail_observed"] = True
        self.report["plan_observations"].append(plan)
        return plan

    def preflight(self) -> None:
        scheduler = self.request("/api/v1/readiness")
        if scheduler.get("status") != "ready" or scheduler.get("startupMode") != "product":
            raise RuntimeError("需要 ready 且 product 模式，脚本不会切换模式")
        if self.request("/api/v1/edge/readiness").get("status") != "ready":
            raise RuntimeError("设备 Runtime 未就绪")
        station = self.request("/api/v1/stations/local/error-handling")
        if station.get("mode") != "RUNNING" or station.get("errors") or station.get("active_session_id"):
            raise RuntimeError("工站暂停或存在未结处置，脚本不会自动恢复")
        self.observe_plan()
        self.check_resources()
        graphs = {label: self.check_graph(label) for label in WORKFLOWS}
        self.report["graph_hashes"] = {
            label: hashlib.sha256(json.dumps(graph, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            for label, graph in graphs.items()}
        self.check_no_foreign_active_tasks()
        self.report["preflight_passed"] = True
        self.save()

    def check_created_task(self, label: str, task: dict[str, Any]) -> None:
        workflow_uuid, node_ids = WORKFLOWS[label]
        if task.get("workflow_uuid") != workflow_uuid or task.get("input", {}).get("hold_seconds") != self.hold_seconds:
            raise RuntimeError("已受理任务的工作流或规范输入不匹配，已记录身份且不自动处置")
        plan = task.get("execution_plan", {}).get("resource_plan", {})
        resources = plan.get("resources", [])
        if (plan.get("binding_state") != "bound" or len(resources) != 3
                or {item.get("alias") for item in resources} != set(EXPECTED_CONFIGS)
                or len({item.get("canonical_key") for item in resources}) != 3):
            raise RuntimeError("已受理任务没有冻结成三资源 bound 连续计划")
        resource_ids = {item["resource_id"] for item in resources}
        intervals = plan.get("intervals", [])
        if len(intervals) != 3 or {item.get("resource_id") for item in intervals} != resource_ids:
            raise RuntimeError("冻结计划的连续区间数量或资源不匹配")
        for interval in intervals:
            if (interval.get("explicit_boundary") is not True
                    or tuple(interval.get("node_uuids", [])) != node_ids
                    or interval.get("acquire_node_uuid") != node_ids[0]
                    or interval.get("release_node_uuid") != node_ids[-1]):
                raise RuntimeError("冻结连续区间没有覆盖全部三步")
        previous = self.report.get("canonical_resources")
        current = sorted(item["canonical_key"] for item in resources)
        if previous is not None and previous != current:
            raise RuntimeError("A/B 实际绑定的三资源集合不同，无法验证争用")
        self.report["canonical_resources"] = current

    def execute(self, timeout_seconds: float) -> None:
        if not self.report.get("preflight_passed"):
            raise RuntimeError("必须先通过完整只读预检")
        self.check_resources()
        for label, (workflow_uuid, _) in WORKFLOWS.items():
            self.check_no_foreign_active_tasks()
            task = self.request("/api/v1/workflow-tasks", {
                "workflow_uuid": workflow_uuid, "run_mode": "normal", "priority": "normal",
                "input": {"hold_seconds": self.hold_seconds},
                "description": f"时间排程连续持锁模拟验收 {self.report['run_id']} {label}",
                "meta_data": {"validation_run_id": self.report["run_id"], "simulation_only": True,
                              "validation_label": label},
            })
            identity = task["uuid"]
            self.report["task_ids"].append(identity)
            self.report["task_labels"][identity] = label
            self.save()
            self.check_created_task(label, task)
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            observation: dict[str, Any] = {"at": datetime.now(timezone.utc).isoformat(), "tasks": {}}
            for identity in self.report["task_ids"]:
                observation["tasks"][identity] = {
                    "task": self.request(f"/api/v1/workflow-tasks/{identity}"),
                    "jobs": self.request(f"/api/v1/workflow-tasks/{identity}/jobs"),
                    "execution_locks": self.request(f"/api/v1/workflow-tasks/{identity}/execution-locks"),
                }
            self.report["run_observations"].append(observation)
            self.observe_plan()
            tasks = [item["task"] for item in observation["tasks"].values()]
            self.save()
            if any(task["status"] in TERMINAL - {"succeeded"} for task in tasks):
                raise RuntimeError("模拟任务未成功；保留 Task 和锁证据，不自动取消、重试或释放")
            if all(task["status"] == "succeeded" for task in tasks):
                self.verify_completed(observation["tasks"])
                return
            time.sleep(0.5)
        raise TimeoutError("等待模拟任务超时；已保存身份，未自动取消或释放")

    def verify_completed(self, completed: dict[str, Any]) -> None:
        intervals = []
        task_creation_times = []
        scope_finish_times = []
        for identity, evidence in completed.items():
            label = self.report["task_labels"][identity]
            expected_nodes = WORKFLOWS[label][1]
            jobs = evidence["jobs"]
            if not isinstance(jobs, list) or any(job.get("status") != "succeeded" for job in jobs):
                raise RuntimeError("任务存在未成功节点，不能认定连续区间已完成")
            physical = [job for job in jobs if job.get("executor_kind") == "device_action"]
            other = [job for job in jobs if job.get("executor_kind") != "device_action"]
            if (len(physical) != 3 or {job.get("workflow_node_uuid") for job in physical} != set(expected_nodes)
                    or any(job.get("executor_kind") not in {"workflow_input", "workflow_output"} for job in other)):
                raise RuntimeError("每个任务须恰好三个设备动作；纯数据输入/输出节点不计入 AAA/BBB")
            ordered = sorted(physical, key=lambda job: expected_nodes.index(job["workflow_node_uuid"]))
            task_creation_times.append(timestamp(evidence["task"].get("create_time")))
            previous_finish = None
            for job in ordered:
                if (job.get("uncertainty_reason") or job.get("error_info")
                        or job.get("retry_of_job_uuid") or job.get("current_attempt") is not True):
                    raise RuntimeError("设备作业有不确定结果、异常或额外重试，不能认定本次直接成功")
                start, finish = timestamp(job.get("started_at")), timestamp(job.get("finished_at"))
                if finish <= start or (previous_finish is not None and start < previous_finish):
                    raise RuntimeError("任务内部三个设备动作的实际区间不是依次完成")
                previous_finish = finish
                intervals.append((start, finish, label, job["uuid"], job["workflow_node_uuid"]))
            scope_finish_times.append(previous_finish)
            locks = evidence["execution_locks"]
            if locks.get("locks") != [] or locks.get("active_device_tenancy_count") != 0:
                raise RuntimeError("实际成功后仍有资源锁或设备托管，不能声明已释放")
            evidence["events"] = self.request(f"/api/v1/workflow-tasks/{identity}/events?limit=200")
        ordered_intervals = sorted(intervals)
        sequence = "".join(item[2] for item in ordered_intervals)
        if sequence not in {"AAABBB", "BBBAAA"}:
            raise RuntimeError(f"实际动作发生跨任务插入：{sequence}")
        if any(right[0] < left[1] for left, right in zip(ordered_intervals, ordered_intervals[1:])):
            raise RuntimeError("不同任务的实际 Job 执行区间发生重叠")
        if max(task_creation_times) >= min(scope_finish_times):
            raise RuntimeError("第二个任务创建时第一个已完成，未形成竞争窗口，不能认定连续持锁验收通过")
        self.report["completed"] = completed
        self.report["actual_sequence"] = sequence
        self.report["actual_job_intervals"] = [
            {"started_at": start.isoformat(), "finished_at": finish.isoformat(), "task_label": label,
             "job_uuid": job, "workflow_node_uuid": node} for start, finish, label, job, node in ordered_intervals]
        self.report["actual_intervals_non_overlapping"] = True
        self.report["task_submission_overlap_confirmed"] = True
        self.report["actual_continuous_scope_released"] = True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true", help="显式创建 A/B 两条 PLC-Sim 连续持锁任务；默认仅只读预检")
    parser.add_argument("--hold-seconds", type=float, default=2.0, help="模拟动作观察窗口，限定 1–2 秒")
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    args = parser.parse_args()
    if not math.isfinite(args.hold_seconds) or not 1 <= args.hold_seconds <= 2:
        parser.error("hold-seconds 必须在 1–2 秒之间")
    if not math.isfinite(args.timeout_seconds) or not 0 < args.timeout_seconds <= 600:
        parser.error("timeout-seconds 必须在 (0,600] 秒之间")
    if args.output.exists() or args.output.with_name(args.output.name + ".tmp").exists():
        parser.error("输出文件已经存在，请使用新路径以保留历史证据")
    validation = ContinuousLiveValidation(args.base_url, args.output, args.hold_seconds)
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
