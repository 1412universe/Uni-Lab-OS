"""工作流编辑器内嵌 Agent：用 LLM 批量生成/修改当前画布。

设计取舍
--------
1. **凭证只在后端**。API Key 从环境变量读取，绝不下发到前端，也不写进日志。
2. **默认 GLM-4.5-Flash（外部 OpenAI 兼容接口），本地接口可选**。前端可传
   `base_url` / `model` 覆盖，用于接本机 Ollama 等；不传则用服务端默认。
3. **实时画布放进最后一条 user 消息**。多段超长 system 时部分兼容接口只保留
   第一条，画布会被挤掉，模型就会声称「看不到 JSON」。
4. **有字符预算**。优先保画布节点表，其次保边，最后才保 skill 文档——宁可少给
   规范，也不能让模型看不到用户真正要改的东西。
5. **回复与动作分离**。模型返回的 Python/JSON 只是建议；真正的应用由前端拿着
   候选图走既有的整图保存流程，不在这里直接改库。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Mapping

from pydantic import BaseModel, Field

logger = logging.getLogger("unilabos.workflow_agent")

# ── 模型端点 ────────────────────────────────────────────────────────────
# 默认走外部 OpenAI 兼容接口（GLM-4.5-Flash）；本地 Ollama 等由前端显式传入。
DEFAULT_BASE_URL = os.environ.get(
    "UNILABOS_WF_AGENT_BASE_URL", "https://llmapi.paratera.com"
).rstrip("/")
DEFAULT_MODEL = os.environ.get("UNILABOS_WF_AGENT_MODEL", "GLM-4.5-Flash")
DEFAULT_API_KEY = os.environ.get("UNILABOS_WF_AGENT_KEY", "")

# 提示词总预算。超长会让模型丢掉画布，因此必须硬性截断。
_PROMPT_BUDGET = 90_000
_REQUEST_TIMEOUT = 120


class WorkflowAgentError(RuntimeError):
    """Agent 调用失败；消息面向用户，不包含凭证。"""


SYSTEM_PROMPT = """你是 Uni-Lab-OS 工作流编辑器的助手，帮助用户阅读、修改、批量生成当前工作流。

工作流的权威模型（务必遵守）：
- 图有 nodes / edges。`edges` 只表达**执行先后**；**没有边的节点会并行执行**。
- 节点的身份是 `uuid`；`meta_data.unilab.authoring_source_order` 是它在源码里的
  语句顺序；`meta_data.unilab.authoring_result_name` 是它的 Python 结果变量名。
- 同一台设备上的动作**即使没有连线也会被设备锁串行**，不同设备可以真并行。
- 修改图时必须保留既有 `uuid`，不要改 `authoring_source_order` 造成重复。

回答要求：
1. 用简洁中文，像实验室搭档；
2. 用户只是提问/聊天时，正常回答，**不要**输出任何图；
3. 用户明确要求改图时，先说明你打算改什么，再输出**完整的**候选图 JSON；
4. 候选图 JSON 必须用 ```json 代码块包裹，且结构为：
   {"nodes": [...], "edges": [...]}
   节点字段与当前画布一致（uuid/name/type/param/meta_data/pose 等）；
5. 不要编造不存在的动作名——只能使用画布中已出现过的 `action_name`；
6. 一次只输出一个候选图。
"""


def _endpoint(base_url: str, model: str, api_key: str) -> tuple[str, dict[str, str], str]:
    """把 base_url 规范成 chat/completions 地址与请求头。

    参数：``base_url`` 可以是 `https://host`、`https://host/v1` 或完整的
    `/v1/chat/completions`；``model``/``api_key`` 仅用于诊断。返回：
    (url, headers, model)。异常：无。
    """

    base = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
    if base.endswith("/chat/completions"):
        url = base
    elif base.endswith("/v1"):
        url = base + "/chat/completions"
    else:
        url = base + "/v1/chat/completions"
    headers = {"Content-Type": "application/json"}
    key = (api_key or DEFAULT_API_KEY).strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return url, headers, model or DEFAULT_MODEL


def _post_chat(
    messages: list[dict[str, str]],
    *,
    base_url: str,
    model: str,
    api_key: str,
    temperature: float = 0.4,
) -> str:
    """调用 OpenAI 兼容的 chat/completions 并返回助手文本。

    参数：``messages`` 是对话；其余是端点与凭据。返回：助手回复正文。
    异常：HTTP 错误、超时或响应结构异常时抛 ``WorkflowAgentError``，消息不含密钥。
    """

    url, headers, resolved_model = _endpoint(base_url, model, api_key)
    payload = json.dumps(
        {
            "model": resolved_model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:300]
        if error.code in (401, 403):
            raise WorkflowAgentError(
                f"模型鉴权失败（HTTP {error.code}）：请检查服务端是否配置了可用的"
                f" UNILABOS_WF_AGENT_KEY，或改用本地模型接口"
            ) from error
        raise WorkflowAgentError(f"模型接口返回错误（HTTP {error.code}）：{detail}") from error
    except urllib.error.URLError as error:
        raise WorkflowAgentError(
            f"无法连接模型接口 {url}：{error.reason}。若使用本地模型，请确认"
            f" Ollama / vLLM 已在对应地址启动"
        ) from error
    except TimeoutError as error:
        raise WorkflowAgentError(f"模型接口超时（{_REQUEST_TIMEOUT}s）") from error

    try:
        data = json.loads(body)
    except json.JSONDecodeError as error:
        raise WorkflowAgentError("模型接口返回的不是 JSON") from error
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise WorkflowAgentError("模型接口未返回 choices")
    message = choices[0].get("message") if isinstance(choices[0], Mapping) else None
    content = message.get("content") if isinstance(message, Mapping) else None
    if not isinstance(content, str) or not content.strip():
        raise WorkflowAgentError("模型返回了空回复")
    return content


def _graph_digest(graph: Mapping[str, Any]) -> str:
    """把画布压成紧凑的文本摘要，控制提示词长度。

    参数：``graph`` 是 ``{"nodes": [...], "edges": [...]}``。返回：逐节点一行
    （顺序、设备、动作、参数数量）加边列表。异常：无；结构不符时按空处理。
    """

    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
    if not nodes:
        return "【当前画布】为空。"

    lines = [f"【当前画布】节点 {len(nodes)} 个，边 {len(edges)} 条。"]
    by_uuid: dict[str, Mapping[str, Any]] = {}
    ordered = []
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        uuid = str(node.get("uuid") or "")
        by_uuid[uuid] = node
        unilab = (node.get("meta_data") or {}).get("unilab") or {}
        order = unilab.get("authoring_source_order")
        ordered.append((order if isinstance(order, int) else 10**6, uuid, node))
    ordered.sort(key=lambda item: (item[0], item[1]))

    for order, uuid, node in ordered:
        unilab = (node.get("meta_data") or {}).get("unilab") or {}
        params = node.get("param") if isinstance(node.get("param"), Mapping) else {}
        device = str(
            unilab.get("executor_binding", {}).get("device_id")
            if isinstance(unilab.get("executor_binding"), Mapping)
            else ""
        ) or "动态选择"
        lines.append(
            f"- [{order}] uuid={uuid[:8]} name={node.get('name')} "
            f"action={node.get('action_name')} device={device} "
            f"params={len(params)} disabled={bool(node.get('disabled'))}"
        )
    if edges:
        pairs = [
            f"{str(edge.get('source_node_uuid'))[:8]}->{str(edge.get('target_node_uuid'))[:8]}"
            for edge in edges
            if isinstance(edge, Mapping)
        ]
        lines.append("【执行顺序边】" + ("；".join(pairs) if pairs else "无"))
        lines.append(
            "提示：没有出现在上面的节点之间没有依赖，会并行执行。"
        )
    return "\n".join(lines)


def _extract_candidate_graph(reply: str) -> dict[str, Any] | None:
    """从回复里取出 ```json 代码块中的候选图。

    参数：``reply`` 是模型回复。返回：含 nodes/edges 的字典，或 ``None``
    （模型只是回答问题、没给图）。异常：JSON 可解析但结构不合契约时返回 None，
    不把坏结构透给前端。
    """

    for block in re.findall(r"```(?:json)?\s*(.*?)```", reply, flags=re.S):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        if isinstance(data, Mapping) and isinstance(data.get("nodes"), list):
            return {"nodes": list(data["nodes"]), "edges": list(data.get("edges") or [])}
    return None


def run_agent(
    message: str,
    *,
    graph: Mapping[str, Any] | None = None,
    history: list[dict[str, Any]] | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """处理一条 Agent 消息并返回回复与可选候选图。

    参数：``message`` 是用户输入；``graph`` 是当前画布；``history`` 是最近对话；
    ``model``/``base_url``/``api_key`` 可覆盖默认端点（用于本地模型）。
    返回：``{ok, reply, candidate, model, elapsed_ms}``；``candidate`` 为 None
    表示模型只做了回答、没有改图意图。异常：模型错误统一转成 ``ok=False`` 的
    结构化响应，不向调用方抛。
    """

    text = (message or "").strip()
    if not text:
        return {"ok": False, "error": "消息为空", "reply": ""}

    resolved_model = (model or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    digest = _graph_digest(graph or {})
    # 预算：system 先留出空间，剩余全给画布；历史只保留最近 4 条。
    system = SYSTEM_PROMPT
    recent = [
        {"role": str(item.get("role") or "user"), "content": str(item.get("content") or "")}
        for item in (history or [])[-4:]
        if isinstance(item, Mapping) and str(item.get("content") or "").strip()
    ]
    used = len(system) + sum(len(item["content"]) for item in recent) + len(text)
    budget = max(4_000, _PROMPT_BUDGET - used)
    if len(digest) > budget:
        digest = digest[:budget] + "\n…（画布摘要已截断，画布过大）"

    messages = [
        {"role": "system", "content": system},
        *recent,
        # 画布必须在最后一条 user 消息里：多段超长 system 时部分接口只留第一条。
        {"role": "user", "content": f"{digest}\n\n【用户消息】{text}"},
    ]

    started = time.monotonic()
    try:
        reply = _post_chat(
            messages,
            base_url=base_url or DEFAULT_BASE_URL,
            model=resolved_model,
            api_key=api_key or "",
        )
    except WorkflowAgentError as error:
        logger.warning("workflow agent failed: %s", error)
        return {
            "ok": False,
            "error": str(error),
            "reply": "",
            "model": resolved_model,
        }

    candidate = _extract_candidate_graph(reply)
    return {
        "ok": True,
        "reply": reply,
        "candidate": candidate,
        "model": resolved_model,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


# ── HTTP 合同 ───────────────────────────────────────────────────────────


class AgentTurnRequest(BaseModel):
    """一轮 Agent 对话请求。

    必须定义在模块级：本模块启用了 `from __future__ import annotations`，函数内
    定义的类型注解会变成字符串前向引用，Pydantic 无法解析并让整个 OpenAPI
    schema 生成失败（表现为任何 /api/openapi.json 请求 500）。
    """

    message: str = Field(description="用户输入")
    graph: dict[str, Any] | None = Field(
        default=None, description="当前画布 {nodes, edges}；缺省时按空画布处理"
    )
    history: list[dict[str, Any]] | None = Field(
        default=None, description="最近若干轮对话，role/content"
    )
    model: str | None = Field(default=None, description="覆盖默认模型名")
    base_url: str | None = Field(
        default=None,
        description="覆盖默认端点；接本机 Ollama 时形如 http://127.0.0.1:11434/v1",
    )
    api_key: str | None = Field(default=None, description="覆盖服务端默认密钥")



def create_workflow_agent_router() -> Any:
    """构建工作流 Agent 的只读路由。

    返回：``APIRouter``，含两个端点——``POST /workflow-agent`` 处理对话、
    ``GET /workflow-agent/config`` 让前端知道默认模型与可用端点形态。
    异常：无；依赖 pydantic 已随 FastAPI 安装。

    Agent 不会自己写库：它只返回候选图，由前端走既有的整图保存。
    """

    from fastapi import APIRouter

    # 不能再带 /api/v1：外层 workflow router 已经挂了同样前缀，重复会出现
    # /api/v1/api/v1/workflow-agent 这种双前缀路径。
    router = APIRouter(tags=["workflow-agent"])

    @router.get(
        "/workflow-agent/config",
        summary="读取工作流 Agent 的默认模型配置",
        description="只暴露默认模型名与端点地址，绝不返回密钥。",
    )
    def agent_config() -> dict[str, Any]:
        return {
            "ok": True,
            "defaultModel": DEFAULT_MODEL,
            "defaultBaseUrl": DEFAULT_BASE_URL,
            "hasServerKey": bool(DEFAULT_API_KEY),
            "localHint": "http://127.0.0.1:11434/v1",
        }

    @router.post(
        "/workflow-agent",
        summary="与工作流 Agent 对话，可返回候选工作流",
        description=(
            "把当前画布摘要与用户消息交给模型；模型若给出候选图，会在 "
            "`candidate` 中返回 {nodes, edges}，由调用方决定是否保存。"
        ),
    )
    def agent_turn(body: AgentTurnRequest) -> dict[str, Any]:
        return run_agent(
            body.message,
            graph=body.graph,
            history=body.history,
            model=body.model,
            base_url=body.base_url,
            api_key=body.api_key,
        )

    return router


__all__ = [
    "WorkflowAgentError",
    "run_agent",
    "create_workflow_agent_router",
    "DEFAULT_MODEL",
    "DEFAULT_BASE_URL",
]
