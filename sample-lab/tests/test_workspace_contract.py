"""生成工作区的包身份、启动文件和教学示例合同。"""

import json
from pathlib import Path

from sample_lab.devices.demo_device import DemoDevice
from unilabos.package_manager import (
    WorkspaceSource,
    compile_package_source,
    compile_workspace_startup,
)


WORKSPACE = Path(__file__).parents[1]


def test_workspace_identity_and_startup_files() -> None:
    source = WorkspaceSource(WORKSPACE)
    plan = compile_workspace_startup(source)
    assert plan.distribution_name == "sample-lab"
    assert plan.import_package == "sample_lab"
    assert plan.workflow_source_count == 1

    graph = json.loads(plan.resolve_graph(plan.default_graph).read_text(encoding="utf-8"))
    assert graph["nodes"][0]["id"] == "demo_device_01"
    assert graph["nodes"][0]["class"] == "community.sample_lab.demo_device"
    assert graph["links"] == []

    catalog = compile_package_source(source)
    assert [item.id for item in catalog.definitions.devices] == ["demo_device"]
    assert [item.id for item in catalog.definitions.workflows] == ["run_demo"]


def test_demo_driver_echoes_text_without_hardware() -> None:
    assert DemoDevice().echo(text="hello") == {
        "success": True,
        "echoed_text": "hello",
    }
