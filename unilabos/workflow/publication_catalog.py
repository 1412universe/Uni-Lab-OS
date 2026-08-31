"""领域包内已发布工作流合同的文件目录。"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import UUID

from unilabos.workflow.source_file_access import (
    StableFileAccessError,
    assert_directory_identity,
    read_regular_path,
)
from unilabos.workflow.source_publication import (
    SourcePublicationConflict,
    SourcePublicationError,
    atomic_publish_source,
)

PUBLICATION_FILE_NAME = "workflow_publications.json"
PUBLICATION_FILE_BYTE_LIMIT = 16 * 1024 * 1024
PUBLICATION_LIMIT = 1000


class WorkflowPublicationCatalogError(RuntimeError):
    """发布合同文件输入、并发或持久化错误。"""

    def __init__(self, code: str) -> None:
        """建立可由服务层稳定映射的目录错误。

        参数：``code`` 是输入、冲突或文件不可用的稳定分类。返回：无。异常：无；
        错误对象只携带稳定分类，不泄漏领域包本地路径或系统异常文本。
        """

        self.code = code
        super().__init__(code)


class WorkflowPublicationCatalog:
    """以领域包 JSON 文件持久保存不可变发布合同。"""

    def __init__(
        self,
        *,
        package_root: Path,
        package_root_identity: tuple[int, int],
    ) -> None:
        """绑定启动时已冻结身份的唯一领域包目录。

        参数：``package_root`` 是领域包根目录，``package_root_identity`` 是发现
        时的设备号和索引节点。返回：无。异常：目录被替换时在首次读写关闭失败。
        """

        self._package_root = Path(package_root)
        self._package_root_identity = package_root_identity
        # 该进程锁串行化同一领域包发布目录的读改写；跨进程竞争仍由文件哈希 CAS
        # 判断，锁本身不充当文件权威或分布式互斥。
        self._lock = threading.RLock()

    def list_entries(self) -> list[dict[str, Any]]:
        """读取全部不可变发布合同。

        参数：无。返回：按工作流 UUID 和发布版本排序的深拷贝条目，调用方修改
        返回值不会改变文件权威。异常：目录身份、文件类型或 JSON 合同不可信时
        抛 ``WorkflowPublicationCatalogError``，不返回部分结果。
        """

        with self._lock:
            entries, _expected_hash = self._read()
            return deepcopy(entries)

    def latest_for_workflow(self, workflow_uuid: str) -> dict[str, Any] | None:
        """读取指定工作流最新发布条目。

        参数：``workflow_uuid`` 是工作流稳定身份。返回：版本号最大的独立条目，
        没有发布记录时返回 ``None``。异常：UUID 或发布文件不合法时抛目录错误。
        """

        # ``identity`` 是查询目标工作流的规范 UUID，只用于筛选合同所属关系。
        identity = _uuid(workflow_uuid)
        matches = [
            entry
            for entry in self.list_entries()
            if entry["contract"]["workflow_uuid"] == identity
        ]
        if not matches:
            return None
        return max(matches, key=lambda entry: int(entry["contract"]["version"]))

    def save(
        self,
        *,
        source_draft_hash: str,
        contract: Mapping[str, Any],
    ) -> None:
        """原子保存一次不可变发布结果。

        参数：``source_draft_hash`` 标识发布时的 Python 源码字节，``contract``
        是完整冻结合同。返回：无。同一合同可幂等重写；身份内容冲突、目录并发或
        文件异常时关闭当前发布。
        """

        entry = _entry(source_draft_hash, contract)
        with self._lock:
            # ``expected_hash`` 固定本轮读取到的完整文件世代；写入必须以它做 CAS，
            # 避免另一个进程新增合同后被当前进程的旧全集覆盖。
            entries, expected_hash = self._read()
            existing = next(
                (
                    item
                    for item in entries
                    if item["contract"]["uuid"] == entry["contract"]["uuid"]
                ),
                None,
            )
            if existing is not None and existing != entry:
                raise WorkflowPublicationCatalogError("conflict")
            if existing == entry:
                return
            if existing is None:
                if len(entries) >= PUBLICATION_LIMIT:
                    raise WorkflowPublicationCatalogError("invalid_input")
                entries.append(entry)
            self._write(entries, expected_hash=expected_hash)

    def delete_workflow(self, workflow_uuid: str) -> None:
        """移除已删除工作流的全部发布合同。

        参数：``workflow_uuid`` 是已经完成定义删除的工作流稳定身份。返回：无；
        没有记录时幂等成功且不重写文件。异常：UUID、目录、并发 CAS 或文件内容
        不合法时抛目录错误；删除失败不会伪造发布文件已清理。
        """

        # ``identity`` 是待清理工作流的规范 UUID；合同 UUID 不参与本次全集筛选。
        identity = _uuid(workflow_uuid)
        with self._lock:
            # ``expected_hash`` 是删除检查时观察到的完整发布文件世代，保证清理
            # 不会覆盖另一进程刚增加的发布合同。
            entries, expected_hash = self._read()
            remaining = [
                entry
                for entry in entries
                if entry["contract"]["workflow_uuid"] != identity
            ]
            if len(remaining) != len(entries):
                self._write(remaining, expected_hash=expected_hash)

    def _read(self) -> tuple[list[dict[str, Any]], str | None]:
        """读取并校验发布目录。

        参数：无。返回：规范条目全集和原始文件 SHA-256；文件缺失表示尚未发布，
        返回空列表与 ``None``。异常：领域包目录被替换、目标不是稳定普通文件、
        超限或内容损坏时抛 ``unavailable``。
        """

        try:
            assert_directory_identity(
                self._package_root,
                self._package_root_identity,
            )
            snapshot = read_regular_path(
                self._package_root / PUBLICATION_FILE_NAME,
                byte_limit=PUBLICATION_FILE_BYTE_LIMIT,
                missing_ok=True,
            )
            if snapshot is None:
                return [], None
            return _decode(snapshot.content), _sha256(snapshot.content)
        except WorkflowPublicationCatalogError:
            raise
        except (OSError, StableFileAccessError):
            raise WorkflowPublicationCatalogError("unavailable") from None

    def _write(
        self,
        entries: list[dict[str, Any]],
        *,
        expected_hash: str | None,
    ) -> None:
        """以 CAS 原子发布完整合同目录快照。

        参数：``entries`` 是待持久化完整集合，``expected_hash`` 是读取时文件世代，
        缺失文件传 ``None``。返回：无。异常：内容非法、并发覆盖、目录身份变化或
        I/O 失败时抛目录错误；临时文件不会成为发布权威。
        """

        normalized = _validate_entries(entries)
        content = (
            json.dumps(
                {"version": 1, "publications": normalized},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        try:
            assert_directory_identity(
                self._package_root,
                self._package_root_identity,
            )
            atomic_publish_source(
                parent_path=self._package_root,
                target_name=PUBLICATION_FILE_NAME,
                content=content,
                byte_limit=PUBLICATION_FILE_BYTE_LIMIT,
                expected_hash=expected_hash,
            )
        except SourcePublicationConflict:
            raise WorkflowPublicationCatalogError("conflict") from None
        except (OSError, SourcePublicationError, StableFileAccessError):
            raise WorkflowPublicationCatalogError("unavailable") from None


def _decode(raw: bytes) -> list[dict[str, Any]]:
    """解析封闭 JSON 文档并拒绝重复键和非有限数值。

    参数：``raw`` 是已通过普通文件和大小检查的字节。返回：规范排序后的发布
    条目。异常：编码、重复键、版本、结构或合同不合法时抛 ``unavailable``。
    """

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        """把 JSON 键值对还原为对象并拒绝重复键。

        参数：``pairs`` 保留解析顺序及重复项。返回：无重复键的字典。异常：发现
        重复键时抛 ``ValueError``，使完整发布目录关闭式失败。
        """

        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=object_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (
        MemoryError,
        RecursionError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
    ):
        raise WorkflowPublicationCatalogError("unavailable") from None
    if not isinstance(document, dict) or set(document) != {"version", "publications"}:
        raise WorkflowPublicationCatalogError("unavailable")
    if document["version"] != 1 or not isinstance(document["publications"], list):
        raise WorkflowPublicationCatalogError("unavailable")
    try:
        return _validate_entries(document["publications"])
    except WorkflowPublicationCatalogError:
        raise WorkflowPublicationCatalogError("unavailable") from None


def _validate_entries(values: list[Any]) -> list[dict[str, Any]]:
    """校验发布条目身份、版本和合同基本结构。

    参数：``values`` 是 JSON 中的发布条目数组。返回：按工作流与版本排序的新
    列表。异常：数量超限、合同 UUID 重复或同一工作流版本重复时抛输入错误。
    """

    if len(values) > PUBLICATION_LIMIT:
        raise WorkflowPublicationCatalogError("invalid_input")
    entries: list[dict[str, Any]] = []
    # ``contract_uuids`` 保证每个不可变合同身份只表示一份内容；
    # ``workflow_versions`` 保证同一工作流的发布序号不会指向两份合同。
    contract_uuids: set[str] = set()
    workflow_versions: set[tuple[str, int]] = set()
    for value in values:
        if not isinstance(value, dict) or set(value) != {
            "source_draft_hash",
            "contract",
        }:
            raise WorkflowPublicationCatalogError("invalid_input")
        entry = _entry(value["source_draft_hash"], value["contract"])
        contract = entry["contract"]
        version_key = (contract["workflow_uuid"], contract["version"])
        if contract["uuid"] in contract_uuids or version_key in workflow_versions:
            raise WorkflowPublicationCatalogError("invalid_input")
        contract_uuids.add(contract["uuid"])
        workflow_versions.add(version_key)
        entries.append(entry)
    return sorted(
        entries,
        key=lambda item: (
            item["contract"]["workflow_uuid"],
            item["contract"]["version"],
        ),
    )


def _entry(source_draft_hash: Any, contract: Any) -> dict[str, Any]:
    """规范化一条源码哈希与完整发布合同。

    参数：``source_draft_hash`` 是发布时 Python 字节摘要，``contract`` 是含冻结
    图的完整私有合同。返回：可安全 JSON 序列化的深拷贝条目。异常：哈希、UUID、
    版本、字段集合或 JSON 值不合法时抛输入错误，原对象不会被修改。
    """

    if not isinstance(source_draft_hash, str):
        raise WorkflowPublicationCatalogError("invalid_input")
    digest = source_draft_hash.removeprefix("sha256:")
    if len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise WorkflowPublicationCatalogError("invalid_input")
    if not isinstance(contract, Mapping):
        raise WorkflowPublicationCatalogError("invalid_input")
    copied = deepcopy(dict(contract))
    required = {
        "uuid",
        "create_time",
        "update_time",
        "meta_data",
        "workflow_uuid",
        "workflow_revision",
        "version",
        "name",
        "tags",
        "node_template_uuid",
        "input_contract",
        "output_contract",
        "executor_requirements",
        "executor_binding_mapping",
        "boundary_mapping",
        "graph_snapshot",
        "source_hash",
        "contract_digest",
        "node_count",
        "edge_count",
    }
    if not required.issubset(copied) or set(copied) - (required | {"description"}):
        raise WorkflowPublicationCatalogError("invalid_input")
    copied["uuid"] = _uuid(copied["uuid"])
    copied["workflow_uuid"] = _uuid(copied["workflow_uuid"])
    copied["node_template_uuid"] = _uuid(copied["node_template_uuid"])
    if (
        isinstance(copied["workflow_revision"], bool)
        or not isinstance(copied["workflow_revision"], int)
        or copied["workflow_revision"] < 1
        or isinstance(copied["version"], bool)
        or not isinstance(copied["version"], int)
        or copied["version"] < 1
    ):
        raise WorkflowPublicationCatalogError("invalid_input")
    try:
        json.dumps(copied, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise WorkflowPublicationCatalogError("invalid_input") from None
    return {"source_draft_hash": source_draft_hash, "contract": copied}


def _uuid(value: Any) -> str:
    """规范化非零 UUID 字符串。

    参数：``value`` 是合同、工作流或模板身份。返回：小写连字符形式 UUID。
    异常：类型、格式或零 UUID 不合法时抛稳定输入错误。
    """

    if not isinstance(value, str):
        raise WorkflowPublicationCatalogError("invalid_input")
    try:
        # ``identity`` 是解析后的领域稳定身份；零 UUID 被保留为缺失哨兵，不能
        # 成为工作流、合同或节点模板的真实身份。
        identity = UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise WorkflowPublicationCatalogError("invalid_input") from None
    if identity.int == 0:
        raise WorkflowPublicationCatalogError("invalid_input")
    return str(identity)


def _sha256(content: bytes) -> str:
    """计算与源码发布器一致的 SHA-256 CAS 令牌。

    参数：``content`` 是本轮稳定读取的完整发布目录字节。返回：带
    ``sha256:`` 前缀的小写摘要，供原子发布比较文件世代。异常：无。
    """

    return "sha256:" + hashlib.sha256(content).hexdigest()


__all__ = [
    "WorkflowPublicationCatalog",
    "WorkflowPublicationCatalogError",
]
