"""单个工作流的 DAG 状态机，对齐 Go dagEngine 的图推进语义。

对应关系（snapshot dag.go）：

- ``build``            ↔ buildTask（依赖表 + 传参边 + 环检测）
- ``ready_nodes``      ↔ canRunNodes（入度 0 且未消费）
- ``mark_finished``    ↔ clearFinishedNode（从依赖表中删除完成节点）
- ``resolve_params``   ↔ parsePreNodeParam（gjson/sjson + ``@@@``）

差异：Go 用协程 + callbackChan 串行推进；Edge 版把「取 ready → 排序 → 下发」交给
service 层在每次触发点统一执行，本类只维护图状态，无并发副作用。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from unilabos.app.scheduler.models import (
    Handle,
    HandlePair,
    NodeState,
    WorkflowNode,
    WorkflowSpec,
    WorkflowState,
)
from unilabos.app.scheduler.param_resolver import resolve_parent_params
from unilabos.workflow.control_expression import (
    ConditionEvaluationError,
    evaluate_condition_expression,
)


class WorkflowCycleError(Exception):
    """对齐 Go code.WorkflowHasCircularErr。"""


class WorkflowRun:
    """一个已提交工作流的运行态。"""

    def __init__(self, spec: WorkflowSpec):
        self.spec = spec
        # 单步任务创建后必须停在首个节点之前；只有显式 step 命令可以临时放行。
        self.state = (
            WorkflowState.PAUSED if spec.run_mode == "step" else WorkflowState.RUNNING
        )

        self._nodes: Dict[str, WorkflowNode] = {}
        # node_id -> 未完成父节点集合（Go d.dependencies 等价，入度表）
        self._pending_parents: Dict[str, Set[str]] = {}
        # 已消费（已进入 ready 下发流程）的节点（Go d.consumedNode 等价）
        self._consumed: Set[str] = set()
        self._node_states: Dict[str, NodeState] = {}
        # node_id -> 传参边（Go d.nodeParentPairs 等价）
        self._parent_pairs: Dict[str, List[HandlePair]] = {}
        # node_id -> 执行返回值（Go nodeMap[..].ReturnInfo.ReturnValue 等价）
        self._ret_values: Dict[str, Any] = {}
        # 取消意图与业务终态分离；在途节点返回成功/失败也不能抹掉用户取消事实。
        self._cancel_requested = False
        self._failed_nodes: Set[str] = set()
        self._node_errors: Dict[str, str] = {}
        self._selected_branches: Dict[str, str | None] = {}

        self._build()

    # ── 构图（Go buildTask 等价） ──────────────────────────────

    def _build(self) -> None:
        spec = self.spec
        for node in spec.nodes:
            if node.disabled:
                continue
            self._nodes[node.id] = node
            self._node_states[node.id] = NodeState.PENDING

        # 三级 handle 寻址：uuid（旧协议）→ (node_id, handle_key)（新协议，
        # 对齐 workflow_edge.source_handle_key + workflow_handle_template 模板内唯一）
        # → 全局唯一 handle_key（payload 未带 node_id 且 key 不歧义时的兜底）。
        by_uuid: Dict[str, Handle] = {}
        by_node_key: Dict[tuple, Handle] = {}
        by_key: Dict[str, Handle] = {}
        ambiguous_keys: set = set()
        for h in spec.handles:
            if h.uuid:
                by_uuid[h.uuid] = h
            if h.handle_key:
                if h.node_id:
                    by_node_key[(h.node_id, h.handle_key)] = h
                if h.handle_key in by_key:
                    ambiguous_keys.add(h.handle_key)
                else:
                    by_key[h.handle_key] = h

        def find_handle(
            handle_uuid: str, node_id: str, handle_key: str
        ) -> Optional[Handle]:
            if handle_uuid and handle_uuid in by_uuid:
                return by_uuid[handle_uuid]
            if handle_key:
                scoped = by_node_key.get((node_id, handle_key))
                if scoped is not None:
                    return scoped
                if handle_key not in ambiguous_keys:
                    return by_key.get(handle_key)
            return None

        children: Dict[str, List[str]] = {}

        for node_id in self._nodes:
            self._pending_parents[node_id] = set()

        for edge in spec.edges:
            src, dst = edge.source_node_id, edge.target_node_id
            # 过滤无效边（Go loadData 里 sourceNodeExist && targetNodeExist）
            if src not in self._nodes or dst not in self._nodes:
                continue
            self._pending_parents[dst].add(src)
            children.setdefault(src, []).append(dst)

            # 传参边过滤规则与 Go buildNodeHandlePair 一致
            source_handle = find_handle(
                edge.source_handle_uuid, src, edge.source_handle_key
            )
            target_handle = find_handle(
                edge.target_handle_uuid, dst, edge.target_handle_key
            )
            if source_handle is None or target_handle is None:
                continue
            if (
                source_handle.data_source != "executor"
                or source_handle.handle_key == "ready"
                or source_handle.data_key == ""
            ):
                continue
            if target_handle.handle_key == "ready" or target_handle.data_key == "":
                continue
            self._parent_pairs.setdefault(dst, []).append(
                HandlePair(
                    source_node_id=src,
                    source_handle=source_handle,
                    target_handle=target_handle,
                )
            )

        self._detect_cycle(children)

    def _detect_cycle(self, children: Dict[str, List[str]]) -> None:
        visited: Set[str] = set()
        rec_stack: Set[str] = set()

        def dfs(node_id: str) -> bool:
            visited.add(node_id)
            rec_stack.add(node_id)
            for child in children.get(node_id, []):
                if child not in visited:
                    if dfs(child):
                        return True
                elif child in rec_stack:
                    return True
            rec_stack.discard(node_id)
            return False

        for node_id in self._nodes:
            if node_id not in visited:
                if dfs(node_id):
                    raise WorkflowCycleError(
                        f"workflow {self.spec.workflow_id} has circular dependency"
                    )

    # ── 推进（Go canRunNodes / clearFinishedNode 等价） ────────

    def ready_nodes(self) -> List[WorkflowNode]:
        """入度 0 且未消费的节点（不修改消费标记，消费发生在 mark_dispatched）。"""
        if self.state is not WorkflowState.RUNNING:
            return []
        ready: List[WorkflowNode] = []
        for node_id, parents in self._pending_parents.items():
            if parents:
                continue
            if node_id in self._consumed:
                continue
            ready.append(self._nodes[node_id])
            self._node_states[node_id] = NodeState.READY
        return ready

    def mark_dispatched(self, node_id: str) -> None:
        """节点已下发（Go canRunNodes 的 consumedNode 标记 + createJobs）。"""
        self._consumed.add(node_id)
        self._node_states[node_id] = NodeState.DISPATCHED

    def mark_finished(self, node_id: str, ret_value: Any = None) -> None:
        """节点成功完成：记录返回值并从依赖表中清除（Go clearFinishedNode）。"""
        if node_id not in self._nodes:
            return
        self._consumed.add(node_id)
        self._ret_values[node_id] = ret_value
        self._node_states[node_id] = NodeState.SUCCESS
        self._pending_parents.pop(node_id, None)
        for parents in self._pending_parents.values():
            parents.discard(node_id)
        if self._is_all_done():
            if self._cancel_requested:
                self.state = WorkflowState.CANCELED
            elif self._failed_nodes:
                self.state = WorkflowState.FAILED
            else:
                self.state = WorkflowState.SUCCESS

    def mark_skipped(self, node_id: str, *, reason: str) -> None:
        """把未激活控制路径节点结算为跳过，并满足其下游结构依赖。"""

        if node_id not in self._nodes:
            raise ValueError(f"条件区域引用未知节点：{node_id}")
        if self._node_states[node_id] is NodeState.DISPATCHED:
            raise ValueError(f"已派发节点不能被条件跳过：{node_id}")
        self._consumed.add(node_id)
        self._node_states[node_id] = NodeState.SKIPPED
        self._node_errors[node_id] = reason
        self._pending_parents.pop(node_id, None)
        for parents in self._pending_parents.values():
            parents.discard(node_id)
        if self.state is WorkflowState.RUNNING and self._is_all_done():
            self.state = WorkflowState.SUCCESS

    def mark_failed(self, node_id: str, *, reason: str = "") -> None:
        """节点失败：整个工作流失败（对齐 Go errChan → jobsCtxCancel 全停语义）。"""
        if node_id not in self._nodes:
            return
        self._consumed.add(node_id)
        self._failed_nodes.add(node_id)
        self._node_states[node_id] = NodeState.FAILED
        if reason:
            self._node_errors[node_id] = reason
        if self._cancel_requested:
            self.state = (
                WorkflowState.CANCELED
                if self._is_all_done()
                else WorkflowState.CANCELING
            )
        else:
            self.state = WorkflowState.FAILED

    def advance_local_controls(self) -> list[dict[str, Any]]:
        """连续求值当前就绪的条件节点，不越过物理执行边界。"""

        evaluations: list[dict[str, Any]] = []
        while self.state is WorkflowState.RUNNING:
            evaluation = self.prepare_local_control()
            if evaluation is None:
                break
            self.commit_local_control(evaluation)
            evaluations.append(evaluation)
        return evaluations

    def prepare_local_control(self) -> dict[str, Any] | None:
        """只读准备下一个条件决定，供持久层在内存提交前落盘。"""

        ready_condition = next(
            (node for node in self.ready_nodes() if node.executor_kind == "condition"),
            None,
        )
        if ready_condition is None:
            return None
        try:
            selected_label, skipped = self._evaluate_condition_node(ready_condition)
        except (ConditionEvaluationError, TypeError, ValueError) as error:
            return {
                "node_id": ready_condition.id,
                "error": ConditionEvaluationError.code,
                "message": str(error),
                "skipped_node_ids": self._pending_after_control_failure(
                    ready_condition.id
                ),
            }
        return {
            "node_id": ready_condition.id,
            "selected_branch": selected_label,
            "skipped_node_ids": skipped,
        }

    def commit_local_control(self, evaluation: dict[str, Any]) -> None:
        """在外部持久投影成功后提交已准备的条件决定。"""

        node_id = str(evaluation.get("node_id") or "")
        skipped = evaluation.get("skipped_node_ids")
        if node_id not in self._nodes or not isinstance(skipped, list):
            raise ValueError("本地条件决定结构无效")
        if evaluation.get("error"):
            self.mark_failed(node_id, reason=ConditionEvaluationError.code)
            reason = ConditionEvaluationError.code
        else:
            selected_label = evaluation.get("selected_branch")
            self._selected_branches[node_id] = (
                str(selected_label) if selected_label is not None else None
            )
            self.mark_finished(node_id, {"selected_branch": selected_label})
            reason = "branch_not_selected"
        for skipped_node_id in skipped:
            self.mark_skipped(str(skipped_node_id), reason=reason)

    def _pending_after_control_failure(self, failed_node_id: str) -> list[str]:
        """只读列出尚未越过派发边界的其余节点。"""

        return [
            node_id
            for node_id in self._nodes
            if node_id != failed_node_id
            and self._node_states[node_id] in {NodeState.PENDING, NodeState.READY}
        ]

    def _evaluate_condition_node(
        self,
        node: WorkflowNode,
    ) -> tuple[str | None, list[str]]:
        """选择首个严格为真的分支并返回所有未选节点。"""

        variables = node.param.get("variables", {})
        bindings = node.param.get("bindings", {})
        branches = node.param.get("branches")
        if (
            not isinstance(variables, dict)
            or not isinstance(bindings, dict)
            or not isinstance(branches, list)
            or not branches
        ):
            raise ConditionEvaluationError("条件区域参数无效")
        variables = dict(variables)
        for name, binding in bindings.items():
            if not isinstance(name, str) or not isinstance(binding, dict):
                raise ConditionEvaluationError("条件变量绑定无效")
            if binding.get("kind") == "node_result":
                source_uuid = str(binding.get("node_uuid") or "")
                if source_uuid not in self._ret_values:
                    raise ConditionEvaluationError(f"条件结果尚未就绪：{name}")
                # 直连 Edge 入口可能绕过 WorkflowSpecCompiler；节点结果绑定必须
                # 无条件覆盖任何预填同名变量，不能被不可信输入遮蔽。
                variables[name] = self._ret_values[source_uuid]
            elif binding.get("kind") == "workflow_input":
                if name not in variables:
                    raise ConditionEvaluationError(f"条件输入不存在：{name}")
            else:
                raise ConditionEvaluationError("条件变量绑定类型无效")
        selected_index: int | None = None
        default_index: int | None = None
        for index, branch in enumerate(branches):
            if not isinstance(branch, dict):
                raise ConditionEvaluationError("条件分支必须是对象")
            condition = branch.get("condition")
            if condition is None:
                if default_index is not None or index != len(branches) - 1:
                    raise ConditionEvaluationError("else 必须是唯一最后分支")
                default_index = index
                continue
            if selected_index is None and evaluate_condition_expression(
                condition,
                variables=variables,
            ):
                selected_index = index
                break
        if selected_index is None:
            selected_index = default_index
        skipped: list[str] = []
        for index, branch in enumerate(branches):
            node_uuids = branch.get("node_uuids")
            if not isinstance(node_uuids, list) or any(
                not isinstance(value, str) for value in node_uuids
            ):
                raise ConditionEvaluationError("条件分支节点集合无效")
            if index != selected_index:
                skipped.extend(node_uuids)
        selected_label = (
            str(branches[selected_index].get("label"))
            if selected_index is not None
            else None
        )
        return selected_label, skipped

    def cancel(self) -> None:
        """停止后续派发，但保留已派发节点直到设备返回明确终态。"""

        self._cancel_requested = True
        for node_id, state in self._node_states.items():
            if state in (NodeState.PENDING, NodeState.READY):
                self._node_states[node_id] = NodeState.CANCELED
        self.state = (
            WorkflowState.CANCELED if self._is_all_done() else WorkflowState.CANCELING
        )

    def mark_canceled(self, node_id: str) -> None:
        """用设备明确取消终态结算一个已派发节点。

        参数：``node_id`` 是当前在途节点身份。返回无。异常：未知节点幂等忽略；
        该方法只消费已派发节点，不会把取消请求误当成安全停止证明。
        """

        if node_id not in self._nodes:
            return
        self._consumed.add(node_id)
        self._node_states[node_id] = NodeState.CANCELED
        self._pending_parents.pop(node_id, None)
        for parents in self._pending_parents.values():
            parents.discard(node_id)
        self.state = (
            WorkflowState.CANCELED if self._is_all_done() else WorkflowState.CANCELING
        )

    def _is_all_done(self) -> bool:
        return all(
            state
            in (
                NodeState.SUCCESS,
                NodeState.SKIPPED,
                NodeState.FAILED,
                NodeState.CANCELED,
            )
            for state in self._node_states.values()
        )

    # ── 传参（Go parsePreNodeParam 等价） ─────────────────────

    def resolve_params(self, node_id: str) -> Any:
        """返回覆写父节点传参后的节点参数（不修改原 spec）。"""
        node = self._nodes[node_id]
        pairs = self._parent_pairs.get(node_id, [])
        if not pairs:
            return node.param
        return resolve_parent_params(node.param, pairs, self._ret_values)

    # ── 查询 ──────────────────────────────────────────────────

    def node(self, node_id: str) -> Optional[WorkflowNode]:
        return self._nodes.get(node_id)

    def node_state(self, node_id: str) -> Optional[NodeState]:
        return self._node_states.get(node_id)

    def ret_value(self, node_id: str) -> Any:
        return self._ret_values.get(node_id)

    def snapshot(self) -> Dict[str, Any]:
        """当前运行态快照（API 查询用；含图结构供前端画布渲染）。"""
        return {
            "workflow_id": self.spec.workflow_id,
            "task_id": self.spec.task_id,
            "state": self.state.value,
            "nodes": {
                node_id: {
                    "state": state.value,
                    "pending_parents": sorted(
                        self._pending_parents.get(node_id, set())
                    ),
                    "device_id": self._nodes[node_id].device_id,
                    "action_name": self._nodes[node_id].action_name,
                    "node_type": self._nodes[node_id].node_type,
                    "reason": self._node_errors.get(node_id),
                    "selected_branch": self._selected_branches.get(node_id),
                }
                for node_id, state in self._node_states.items()
            },
            "edges": [
                {"source": e.source_node_id, "target": e.target_node_id}
                for e in self.spec.edges
                if e.source_node_id in self._nodes and e.target_node_id in self._nodes
            ],
        }


__all__ = ["WorkflowCycleError", "WorkflowRun"]
