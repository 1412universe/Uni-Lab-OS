#!/usr/bin/env python3
"""一键准备环境并运行「标准样品称量分装」，不需要打开网页。"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GRAPH = ROOT / "deployment" / "graphs" / "dry-run.json"
WORKFLOW_UUID = "7e2a9c14-6b5d-4f81-a3c0-91d8e4b2f6a5"
TERMINAL = {"succeeded", "failed", "cancelled", "canceled", "aborted"}


def say(message: str) -> None:
    print(message, flush=True)


def fail(message: str, code: int = 1) -> None:
    say(f"没有完成：{message}")
    raise SystemExit(code)


def run_unilab(*args: str, wait: bool = True) -> subprocess.CompletedProcess[str]:
    command = ["unilab", *args]
    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)


def workspace_status() -> dict:
    result = run_unilab("workspace", "status", "--workspace", str(ROOT), "--json")
    if result.returncode != 0:
        return {}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}


def backend_ready(status: dict) -> bool:
    backend = ((status.get("components") or {}).get("backend") or {})
    return backend.get("phase") == "ready" and bool(backend.get("address"))


def backend_address(status: dict | None = None) -> str:
    current = status if status is not None else workspace_status()
    address = ((current.get("components") or {}).get("backend") or {}).get("address")
    if not address:
        fail("工作区还没有准备好。先执行 python scripts/run_demo.py --open-frontend 启动，或先 unilab workspace stop --workspace . 再重试。")
    return str(address).rstrip("/")


def api_root(status: dict | None = None) -> str:
    return f"{backend_address(status)}/api/v1"


def console_url(status: dict | None = None) -> str:
    return f"{backend_address(status)}/console/"


def open_frontend(status: dict | None = None) -> str:
    url = console_url(status)
    say(f"操作页面地址：{url}")
    say("本包默认不会自动弹浏览器。正在尝试打开……")
    opened = webbrowser.open(url)
    if not opened:
        say("没有自动打开浏览器。请把上面这一行地址复制到浏览器地址栏。")
    return url


def request_json(method: str, url: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"} if body is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        fail(f"接口返回 {error.code}。{detail[:500]}")
    except urllib.error.URLError as error:
        fail(f"连不上工作区：{error.reason}")


def occupancy(api: str) -> dict[str, str]:
    nodes = request_json("GET", f"{api}/materials/graph")["data"]["nodes"]
    names = {node["material"]["uuid"]: node["material"]["name"] for node in nodes}
    filled: dict[str, str] = {}
    for node in nodes:
        for site in node.get("sites") or []:
            occupant = site.get("occupied_material_uuid")
            if occupant:
                filled[str(site.get("name"))] = names.get(occupant, occupant)
    return filled


def sources_ready(filled: dict[str, str]) -> bool:
    return "标准样品母液 001" in filled.get("S01", "") and "空分装瓶 001" in filled.get("E01", "")


def cancel_active_tasks(api: str) -> None:
    items = request_json("GET", f"{api}/workflow-tasks?page=1&page_size=50")["data"]["items"]
    for task in items:
        if task.get("status") in {"running", "pending", "paused"}:
            request_json(
                "POST",
                f"{api}/workflow-tasks/{task['uuid']}/commands",
                {"type": "cancel", "idempotency_key": str(uuid.uuid4())},
            )


def ensure_unilab() -> None:
    if shutil.which("unilab"):
        return
    fail("当前窗口里找不到 unilab。先执行 conda activate unilab（环境名以本机为准），确认输入 unilab 能看到命令说明后再跑。")


def ensure_package_installed() -> None:
    try:
        import demo_lab  # noqa: F401
    except ImportError:
        say("第一次使用，正在把演示包装进当前环境……")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", str(ROOT)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            fail(f"安装演示包失败。\n{result.stderr[-800:]}")


def start_workspace() -> dict:
    say("正在启动演示工作区，大约需要半分钟……")
    result = run_unilab(
        "workspace",
        "start",
        "--workspace",
        str(ROOT),
        "--graph",
        str(GRAPH),
        "--runtime-mode",
        "normal",
        "--startup-mode",
        "develop",
        "--wait",
        "300",
        "--json",
    )
    if result.returncode != 0:
        fail(f"启动失败。\n{(result.stderr or result.stdout)[-800:]}")
    status = workspace_status()
    if not backend_ready(status):
        fail("启动后工作区仍未就绪。执行 unilab workspace stop --workspace . 后再运行 python scripts/run_demo.py。")
    return status


def reset_workspace() -> dict:
    say("物料不在起始位置，正在恢复后再跑……")
    status = workspace_status()
    if backend_ready(status):
        try:
            cancel_active_tasks(api_root(status))
        except SystemExit:
            pass
    result = run_unilab(
        "workspace",
        "reset-local",
        "--workspace",
        str(ROOT),
        "--graph",
        str(GRAPH),
        "--runtime-mode",
        "normal",
        "--yes",
        "--wait",
        "180",
        "--json",
    )
    if result.returncode != 0:
        fail(f"恢复物料失败。\n{(result.stderr or result.stdout)[-800:]}")
    return start_workspace()


def publish_workflow(api: str) -> None:
    workflow = request_json("GET", f"{api}/workflows/{WORKFLOW_UUID}")["data"]
    if workflow.get("status") == "published":
        say("工作流已经发布，可以直接运行。")
        return
    authoring = request_json("GET", f"{api}/workflows/{WORKFLOW_UUID}/authoring")["data"]
    if authoring.get("state") != "applied":
        fail(f"工作流还不能发布，当前状态是 {authoring.get('state')}。")
    say("第一次运行，正在发布「标准样品称量分装」……")
    request_json(
        "POST",
        f"{api}/workflows/{WORKFLOW_UUID}/publications",
        {"revision": int(authoring["workflow_revision"])},
    )


def run_workflow(api: str, sample_id: str, volume_ul: int) -> dict:
    task_input = {
        "sample_id": sample_id,
        "target_aliquot_volume_ul": volume_ul,
        "source_stock_site": "S01",
        "source_empty_site": "E01",
        "tip_box_site": "T01",
        "used_stock_target_site": "U01",
        "product_vial_target_site": "F01",
    }
    say("正在检查这次能不能跑……")
    preflight = request_json(
        "POST",
        f"{api}/workflows/{WORKFLOW_UUID}/run-preflight",
        {"run_mode": "normal", "input": task_input},
    )["data"]
    if not preflight.get("can_run"):
        fail(f"预检没有通过：{preflight.get('status')}。先执行 python scripts/run_demo.py --reset，再按原参数跑一遍。")
    say(f"开始运行。样品编号 {sample_id}，分装体积 {volume_ul} 微升。")
    created = request_json(
        "POST",
        f"{api}/workflow-tasks",
        {
            "workflow_uuid": WORKFLOW_UUID,
            "run_mode": "normal",
            "priority": "normal",
            "input": task_input,
            "inventory_bindings": [],
            "description": f"标准样品称量分装-{sample_id}",
        },
    )["data"]
    task_uuid = created["uuid"]
    last = None
    for _ in range(90):
        task = request_json("GET", f"{api}/workflow-tasks/{task_uuid}")["data"]
        status = task.get("status")
        if status != last:
            say(f"当前进度：{status}")
            last = status
        if status in TERMINAL:
            return task
        time.sleep(1)
    fail(f"等了太久还没有结束，任务编号 {task_uuid}。")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="运行标准样品称量分装演示。不需要打开网页。",
    )
    parser.add_argument("--sample-id", default="ASSAY-2026-001", help="样品编号，会出现在封盖结果里")
    parser.add_argument("--volume-ul", type=int, default=200, help="分装体积，单位微升，范围 1 到 5000")
    parser.add_argument("--reset", action="store_true", help="先把物料恢复到起始位置再跑")
    parser.add_argument(
        "--open-frontend",
        action="store_true",
        help="启动工作区并打开操作页面，不自动创建任务",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not 1 <= args.volume_ul <= 5000:
        fail("分装体积必须在 1 到 5000 微升之间。")
    if not GRAPH.is_file():
        fail("找不到启动图，请确认你是在完整的 demo-lab 文件夹里运行。")

    ensure_unilab()
    ensure_package_installed()

    status = workspace_status()
    if args.reset or not backend_ready(status):
        status = reset_workspace() if args.reset else start_workspace()
    api = api_root(status)

    if args.open_frontend:
        if args.reset:
            filled = occupancy(api)
            if not sources_ready(filled):
                fail("恢复后原料位仍然是空的。执行 unilab workspace stop --workspace . 后再运行 python scripts/run_demo.py --reset。")
        publish_workflow(api)
        open_frontend(status)
        say("页面打开后，在工作流里找到「标准样品称量分装」。")
        say("第一次请先发布，再填写样品编号和分装体积，做运行准备，通过后再创建任务。")
        return

    filled = occupancy(api)
    if not sources_ready(filled):
        status = reset_workspace()
        api = api_root(status)
        filled = occupancy(api)
        if not sources_ready(filled):
            fail("恢复后原料位仍然是空的。执行 unilab workspace stop --workspace . 后再运行 python scripts/run_demo.py --reset。")

    publish_workflow(api)
    task = run_workflow(api, args.sample_id, args.volume_ul)
    output = task.get("output") or {}
    if task.get("status") != "succeeded":
        fail(f"任务没有成功，状态是 {task.get('status')}。任务编号 {task.get('uuid')}。")

    say("")
    say("运行成功。")
    say(f"样品编号：{args.sample_id}")
    say(f"称量结果：{output.get('measured_mass_g')} 克")
    say(f"分装体积：{output.get('commanded_volume_ul')} 微升")
    say(f"封盖说明：{output.get('message')}")
    say("成品瓶已放到成品区 F01，用过的母液已放到已用区 U01。")
    say("下次直接再执行同一条命令即可，脚本会自动恢复物料。")


if __name__ == "__main__":
    main()
