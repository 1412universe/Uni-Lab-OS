"""不可变已发布工作流合同（PublishedWorkflowContract）的本地权威。"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Mapping
from typing import Any
from uuid import UUID, uuid4, uuid5

import rfc8785

from unilabos.workflow.json_codec import decode_json_bytes, encode_json
from unilabos.workflow.handle_projection import workflow_handle_type
from unilabos.workflow.store import WorkflowStore, utc_now
from unilabos.workflow.workflow_io import (
    WorkflowIOValidationError,
    validate_workflow_graph_io,
)

_DIGEST_PREFIX = "sha256:"
_COMPOSITE_HOST_RESOURCE_TEMPLATE_UUID = "00000000-0000-5000-8000-000000000052"


class PublishedContractConflict(RuntimeError):
    """发布修订或既有不可变内容发生冲突。"""


class PublishedContractInvalid(ValueError):
    """来源工作流不能形成完整的发布合同。"""


def _json(value: Any) -> str:
    """把 JSON 值编码为稳定 SQLite 文本。"""

    return encode_json(value, sort_keys=True).decode("utf-8")


def _load(value: str) -> Any:
    """从 SQLite 文本恢复 JSON 值。"""

    return decode_json_bytes(value.encode("utf-8"))


def _digest(value: Any) -> str:
    """按 RFC 8785 规范 JSON 计算带算法前缀的 SHA-256。"""

    encoded = rfc8785.dumps(value)
    return _DIGEST_PREFIX + hashlib.sha256(encoded).hexdigest()


def _canonical_graph(graph: Mapping[str, Any]) -> dict[str, Any]:
    """复制并稳定排序完整工作流图，避免摘要依赖数据库返回顺序。"""

    return {
        "workflow": dict(graph["workflow"]),
        "nodes": sorted(
            (dict(item) for item in graph.get("nodes", [])),
            key=lambda item: str(item["uuid"]),
        ),
        "edges": sorted(
            (dict(item) for item in graph.get("edges", [])),
            key=lambda item: str(item["uuid"]),
        ),
        "node_templates": sorted(
            (dict(item) for item in graph.get("node_templates", [])),
            key=lambda item: str(item["uuid"]),
        ),
        "handle_templates": sorted(
            (dict(item) for item in graph.get("handle_templates", [])),
            key=lambda item: str(item["uuid"]),
        ),
    }


def _abstract_executor_requirements(
    snapshot: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """把来源图中的具体设备物料抽象为父调用必须提供的执行器要求。

    参数：``snapshot`` 是独立冻结图，会原地清除设备节点的 ``material_uuid``。
    返回稳定要求数组和“来源节点→要求 key”映射；缺少模板或同一设备跨不兼容
    资源模板使用时抛 ``PublishedContractInvalid``。
    """

    templates = {
        str(item["uuid"]): item
        for item in snapshot["node_templates"]
        if isinstance(item, Mapping) and isinstance(item.get("uuid"), str)
    }
    groups: dict[str, dict[str, Any]] = {}
    requirements: list[dict[str, Any]] = []
    mapping: dict[str, str] = {}
    for node in snapshot["nodes"]:
        material_uuid = node.get("material_uuid")
        if material_uuid is None:
            continue
        template_uuid = node.get("workflow_node_template_uuid")
        template = templates.get(str(template_uuid))
        resource_template_uuid = (
            template.get("resource_template_uuid")
            if isinstance(template, Mapping)
            else None
        )
        if not isinstance(resource_template_uuid, str):
            raise PublishedContractInvalid("设备节点缺少可发布的资源模板")
        group = groups.get(str(material_uuid))
        if group is None:
            position = len(requirements) + 1
            group = {
                "key": f"executor_{position}",
                "display_name": f"执行设备 {position}",
                "resource_template_uuid": resource_template_uuid,
                "required": True,
            }
            groups[str(material_uuid)] = group
            requirements.append(group)
        elif group["resource_template_uuid"] != resource_template_uuid:
            raise PublishedContractInvalid("同一设备通过不兼容资源模板参与工作流")
        mapping[str(node["uuid"])] = str(group["key"])
        node["material_uuid"] = None
    return requirements, mapping


def _boundary_contracts(
    graph: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], Any]:
    """从冻结图投影 Backend 形状的输入、输出合同。

    参数：``graph`` 是同一修订的完整工作流图。返回版本化输入与输出对象；图的
    连接点不闭合或类型不兼容时抛 ``PublishedContractInvalid``。
    """

    try:
        workflow_io = validate_workflow_graph_io(graph)
    except (KeyError, TypeError, ValueError, WorkflowIOValidationError):
        raise PublishedContractInvalid("工作流输入输出合同不完整") from None
    inputs = workflow_io.input_contract.to_dict().get("parameters", [])
    outputs = workflow_io.output_contract.to_dict().get("outputs", [])
    return (
        {"version": 1, "inputs": inputs},
        {"version": 1, "outputs": outputs},
        workflow_io,
    )


def _handle_uuid(template_uuid: str, io_type: str, key: str) -> str:
    """为发布模板的连接点生成确定性 UUID。"""

    return str(uuid5(UUID(template_uuid), f"published-handle:{io_type}:{key}"))


def _published_template_projection(
    *,
    contract_uuid: str,
    node_template_uuid: str,
    graph: Mapping[str, Any],
    input_contract: Mapping[str, Any],
    output_contract: Mapping[str, Any],
    workflow_io: Any,
    source_hash: str,
    contract_digest: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """构造发布模板、公开连接点及服务端边界映射。"""

    workflow = graph["workflow"]
    handles: list[dict[str, Any]] = []
    input_handle_by_name: dict[str, str] = {}
    output_handle_by_name: dict[str, str] = {}
    for descriptor in input_contract["inputs"]:
        name = str(descriptor["name"])
        handle_uuid = _handle_uuid(node_template_uuid, "target", name)
        input_handle_by_name[name] = handle_uuid
        handles.append(
            {
                "uuid": handle_uuid,
                "handle_key": name,
                "io_type": "target",
                "display_name": str(descriptor.get("title") or name),
                "description": descriptor.get("description"),
                "type": workflow_handle_type(descriptor["schema"]),
                "required": bool(descriptor.get("required", False)),
                "data_source": "goal",
                "data_key": name,
                "meta_data": {"unilab": {"value_schema": descriptor["schema"]}},
            }
        )
    for descriptor in output_contract["outputs"]:
        name = str(descriptor["name"])
        handle_uuid = _handle_uuid(node_template_uuid, "source", name)
        output_handle_by_name[name] = handle_uuid
        handles.append(
            {
                "uuid": handle_uuid,
                "handle_key": name,
                "io_type": "source",
                "display_name": str(descriptor.get("title") or name),
                "description": descriptor.get("description"),
                "type": workflow_handle_type(descriptor["schema"]),
                "required": False,
                "data_source": "result",
                "data_key": name,
                "meta_data": {"unilab": {"value_schema": descriptor["schema"]}},
            }
        )
    for io_type in ("target", "source"):
        handles.append(
            {
                "uuid": _handle_uuid(node_template_uuid, io_type, "ready"),
                "handle_key": "ready",
                "io_type": io_type,
                "display_name": "ready",
                "description": None,
                "type": "default",
                "required": False,
                "data_source": None,
                "data_key": None,
                "meta_data": {},
            }
        )

    target_mappings: dict[str, list[dict[str, str]]] = {
        handle_uuid: [] for handle_uuid in input_handle_by_name.values()
    }
    for node_uuid, bindings in workflow_io.input_bindings.items():
        for target_handle_uuid, binding in bindings.items():
            parameter = str(binding["parameter"])
            boundary_handle_uuid = input_handle_by_name[parameter]
            target_mappings[boundary_handle_uuid].append(
                {
                    "workflow_node_uuid": node_uuid,
                    "target_handle_uuid": target_handle_uuid,
                }
            )
    source_mappings = {
        output_handle_by_name[name]: dict(binding)
        for name, binding in workflow_io.output_bindings.items()
        if name in output_handle_by_name
    }
    boundary_mapping = {
        "target_mappings": target_mappings,
        "source_mappings": source_mappings,
        "structural_mappings": {
            "entry_targets": [],
            "completion_sources": [],
        },
    }
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "goal": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    str(item["name"]): item["schema"]
                    for item in input_contract["inputs"]
                },
                "required": [
                    str(item["name"])
                    for item in input_contract["inputs"]
                    if item.get("required") is True
                ],
            },
            "result": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    str(item["name"]): item["schema"]
                    for item in output_contract["outputs"]
                },
                "required": [
                    str(item["name"]) for item in output_contract["outputs"]
                ],
            },
        },
        "required": ["goal", "result"],
        "x-unilabos-workflow-contract": {
            "version": 1,
            "workflow_uuid": workflow["uuid"],
            "workflow_revision": workflow["revision"],
            "source_hash": source_hash,
            "contract_digest": contract_digest,
        },
    }
    template = {
        "uuid": node_template_uuid,
        "description": workflow.get("description"),
        "meta_data": {
            "unilab": {
                "framework_owner_only": True,
                "workflow_contract": {
                    "version": 1,
                    "contract_uuid": contract_uuid,
                    "workflow_uuid": workflow["uuid"],
                    "workflow_revision": workflow["revision"],
                    "source_hash": source_hash,
                    "contract_digest": contract_digest,
                },
            }
        },
        "resource_template_uuid": _COMPOSITE_HOST_RESOURCE_TEMPLATE_UUID,
        "name": f"workflow:{workflow['uuid']}:r{workflow['revision']}",
        "display_name": workflow["name"],
        "class": None,
        "goal": {str(item["name"]): str(item["name"]) for item in input_contract["inputs"]},
        "goal_default": {},
        "feedback": {},
        "result": {str(item["name"]): str(item["name"]) for item in output_contract["outputs"]},
        "schema": schema,
        "type": "workflow",
        "node_type": "workflow",
    }
    return template, handles, boundary_mapping


class PublishedWorkflowContractStore:
    """在 ``WorkflowStore`` 同一 SQLite 权威内持久化不可变发布记录。"""

    def __init__(self, store: WorkflowStore) -> None:
        """创建发布仓储并幂等补齐独立表。

        参数：``store`` 是本地工作流唯一写模型。返回：无。异常：DDL 或事务失败
        原样传播，禁止在表未就绪时继续提供发布接口。
        """

        self._store = store
        with store.transaction() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS published_workflow_contract (
                    uuid TEXT PRIMARY KEY,
                    create_time TEXT NOT NULL,
                    update_time TEXT NOT NULL,
                    deleted_at TEXT,
                    description TEXT,
                    meta_data TEXT NOT NULL,
                    workflow_uuid TEXT NOT NULL,
                    workflow_revision INTEGER NOT NULL CHECK (workflow_revision > 0),
                    version INTEGER NOT NULL CHECK (version > 0),
                    name TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    node_template_uuid TEXT NOT NULL,
                    input_contract TEXT NOT NULL,
                    output_contract TEXT NOT NULL,
                    executor_requirements TEXT NOT NULL,
                    executor_binding_mapping TEXT NOT NULL,
                    boundary_mapping TEXT NOT NULL,
                    graph_snapshot TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    contract_digest TEXT NOT NULL,
                    node_count INTEGER NOT NULL CHECK (node_count > 0),
                    edge_count INTEGER NOT NULL CHECK (edge_count >= 0),
                    FOREIGN KEY(workflow_uuid) REFERENCES workflow(uuid),
                    UNIQUE(workflow_uuid, workflow_revision),
                    UNIQUE(workflow_uuid, version)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS ix_published_workflow_contract_latest
                    ON published_workflow_contract(
                        workflow_uuid, version DESC, create_time DESC
                    ) WHERE deleted_at IS NULL
                """
            )

    def publish(
        self,
        *,
        graph: Mapping[str, Any],
        expected_revision: int,
    ) -> dict[str, Any]:
        """幂等冻结当前修订，并在同一工作流内递增发布版本。

        参数：``graph`` 是服务层验证后的完整图，``expected_revision`` 是调用方
        乐观并发版本。返回包含私有冻结字段的合同；当前修订变化或同修订内容漂移
        时抛 ``PublishedContractConflict``。
        """

        snapshot = _canonical_graph(graph)
        workflow = snapshot["workflow"]
        workflow_uuid = str(workflow["uuid"])
        if int(workflow["revision"]) != expected_revision:
            raise PublishedContractConflict("工作流修订已变化")
        if not snapshot["nodes"]:
            raise PublishedContractInvalid("已发布工作流至少需要一个节点")
        input_contract, output_contract, workflow_io = _boundary_contracts(snapshot)
        executor_requirements, executor_binding_mapping = (
            _abstract_executor_requirements(snapshot)
        )
        boundary_mapping: dict[str, Any] = {}
        source_hash = _digest(
            {
                "graph": snapshot,
                "executor_requirements": executor_requirements,
                "executor_binding_mapping": executor_binding_mapping,
            }
            if executor_requirements or executor_binding_mapping
            else snapshot
        )
        contract_digest = _digest(
            {
                "version": 1,
                "inputs": input_contract["inputs"],
                "outputs": output_contract["outputs"],
                "executor_requirements": executor_requirements,
            }
        )

        with self._store.transaction() as connection:
            source = connection.execute(
                """
                SELECT revision FROM workflow
                WHERE uuid = ? AND deleted_at IS NULL
                """,
                (workflow_uuid,),
            ).fetchone()
            if source is None or int(source["revision"]) != expected_revision:
                raise PublishedContractConflict("工作流修订已变化")
            existing = connection.execute(
                """
                SELECT * FROM published_workflow_contract
                WHERE workflow_uuid = ? AND workflow_revision = ?
                  AND deleted_at IS NULL
                """,
                (workflow_uuid, expected_revision),
            ).fetchone()
            if existing is not None:
                result = self._row(existing)
                if (
                    result["source_hash"] != source_hash
                    or result["contract_digest"] != contract_digest
                ):
                    raise PublishedContractConflict("同一修订的发布内容不一致")
                return result
            latest_version = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(version), 0)
                    FROM published_workflow_contract
                    WHERE workflow_uuid = ? AND deleted_at IS NULL
                    """,
                    (workflow_uuid,),
                ).fetchone()[0]
            )
            now = utc_now()
            contract_uuid = str(uuid4())
            node_template_uuid = str(uuid4())
            template, handles, boundary_mapping = _published_template_projection(
                contract_uuid=contract_uuid,
                node_template_uuid=node_template_uuid,
                graph=snapshot,
                input_contract=input_contract,
                output_contract=output_contract,
                workflow_io=workflow_io,
                source_hash=source_hash,
                contract_digest=contract_digest,
            )
            authority_id = f"published-workflow-contract:{contract_uuid}"
            connection.execute(
                """
                INSERT INTO workflow_node_template(
                    uuid, create_time, update_time, deleted_at, description,
                    meta_data, authority_id, resource_template_uuid, name,
                    display_name, class, goal, goal_default, feedback, result,
                    schema, type, icon, header, footer, node_type
                ) VALUES (
                    ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    NULL, NULL, NULL, ?
                )
                """,
                (
                    node_template_uuid,
                    now,
                    now,
                    template["description"],
                    _json(template["meta_data"]),
                    authority_id,
                    template["resource_template_uuid"],
                    template["name"],
                    template["display_name"],
                    template["class"],
                    _json(template["goal"]),
                    _json(template["goal_default"]),
                    _json(template["feedback"]),
                    _json(template["result"]),
                    _json(template["schema"]),
                    template["type"],
                    template["node_type"],
                ),
            )
            for handle in handles:
                connection.execute(
                    """
                    INSERT INTO workflow_handle_template(
                        uuid, create_time, update_time, deleted_at, description,
                        meta_data, authority_id, workflow_node_template_uuid,
                        handle_key, io_type, display_name, type, required,
                        data_source, data_key
                    ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        handle["uuid"],
                        now,
                        now,
                        handle["description"],
                        _json(handle["meta_data"]),
                        authority_id,
                        node_template_uuid,
                        handle["handle_key"],
                        handle["io_type"],
                        handle["display_name"],
                        handle["type"],
                        int(handle["required"]),
                        handle["data_source"],
                        handle["data_key"],
                    ),
                )
            values = (
                contract_uuid,
                now,
                now,
                workflow.get("description"),
                _json({}),
                workflow_uuid,
                expected_revision,
                latest_version + 1,
                str(workflow["name"]),
                _json(workflow.get("tags", [])),
                node_template_uuid,
                _json(input_contract),
                _json(output_contract),
                _json(executor_requirements),
                _json(executor_binding_mapping),
                _json(boundary_mapping),
                _json(snapshot),
                source_hash,
                contract_digest,
                len(snapshot["nodes"]),
                len(snapshot["edges"]),
            )
            connection.execute(
                """
                INSERT INTO published_workflow_contract(
                    uuid, create_time, update_time, deleted_at, description,
                    meta_data, workflow_uuid, workflow_revision, version, name,
                    tags, node_template_uuid, input_contract, output_contract,
                    executor_requirements, executor_binding_mapping,
                    boundary_mapping, graph_snapshot, source_hash,
                    contract_digest, node_count, edge_count
                ) VALUES (
                    ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                values,
            )
            row = connection.execute(
                "SELECT * FROM published_workflow_contract WHERE uuid = ?",
                (contract_uuid,),
            ).fetchone()
            assert row is not None
            return self._row(row)

    def get(self, contract_uuid: str) -> dict[str, Any]:
        """读取含冻结图的单个合同；合同不存在时抛 ``KeyError``。"""

        with self._store._lock:
            row = self._store._conn.execute(
                """
                SELECT * FROM published_workflow_contract
                WHERE uuid = ? AND deleted_at IS NULL
                """,
                (contract_uuid,),
            ).fetchone()
        if row is None:
            raise KeyError(contract_uuid)
        return self._row(row)

    def list_latest(
        self,
        *,
        page: int,
        page_size: int,
        keyword: str,
    ) -> dict[str, Any]:
        """分页返回每个来源工作流的最新完整发布合同。"""

        where = """
            published.deleted_at IS NULL
            AND NOT EXISTS (
                SELECT 1 FROM published_workflow_contract AS newer
                WHERE newer.workflow_uuid = published.workflow_uuid
                  AND newer.version > published.version
                  AND newer.deleted_at IS NULL
            )
        """
        values: list[Any] = []
        normalized_keyword = keyword.strip().lower()
        if normalized_keyword:
            where += " AND LOWER(published.name) LIKE ?"
            values.append(f"%{normalized_keyword}%")
        offset = (page - 1) * page_size
        with self._store._lock:
            total = int(
                self._store._conn.execute(
                    f"SELECT COUNT(*) FROM published_workflow_contract AS published WHERE {where}",
                    values,
                ).fetchone()[0]
            )
            rows = self._store._conn.execute(
                f"""
                SELECT published.*
                FROM published_workflow_contract AS published
                WHERE {where}
                ORDER BY published.create_time DESC, published.uuid DESC
                LIMIT ? OFFSET ?
                """,
                (*values, page_size, offset),
            ).fetchall()
        return {
            "items": [self._public(self._row(row)) for row in rows],
            "has_more": page * page_size < total,
            "page": page,
            "page_size": page_size,
        }

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        """把 SQLite 行恢复为完整领域合同。"""

        result = {
            "uuid": row["uuid"],
            "create_time": row["create_time"],
            "update_time": row["update_time"],
            "meta_data": _load(row["meta_data"]),
            "workflow_uuid": row["workflow_uuid"],
            "workflow_revision": row["workflow_revision"],
            "version": row["version"],
            "name": row["name"],
            "tags": _load(row["tags"]),
            "node_template_uuid": row["node_template_uuid"],
            "input_contract": _load(row["input_contract"]),
            "output_contract": _load(row["output_contract"]),
            "executor_requirements": _load(row["executor_requirements"]),
            "executor_binding_mapping": _load(row["executor_binding_mapping"]),
            "boundary_mapping": _load(row["boundary_mapping"]),
            "graph_snapshot": _load(row["graph_snapshot"]),
            "source_hash": row["source_hash"],
            "contract_digest": row["contract_digest"],
            "node_count": row["node_count"],
            "edge_count": row["edge_count"],
        }
        if row["description"] is not None:
            result["description"] = row["description"]
        return result

    @staticmethod
    def _public(contract: Mapping[str, Any]) -> dict[str, Any]:
        """隐藏组合展开私有映射与冻结图，只返回 Backend 公共字段。"""

        hidden = {
            "executor_binding_mapping",
            "boundary_mapping",
            "graph_snapshot",
        }
        return {key: value for key, value in contract.items() if key not in hidden}

    def public(self, contract: Mapping[str, Any]) -> dict[str, Any]:
        """返回一个不暴露内部冻结字段的独立公共投影。"""

        return self._public(contract)


__all__ = [
    "PublishedContractConflict",
    "PublishedContractInvalid",
    "PublishedWorkflowContractStore",
]
