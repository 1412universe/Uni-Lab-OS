from setuptools import find_packages, setup

package_name = 'unilabos'

setup(
    name=package_name,
    version='0.11.4',
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        'setuptools',
        'jsonschema>=4.18',
        'rfc8785>=0.1.4,<0.2',
        'msgcenterpy>=0.1.8,<0.2',
        'pylabrobot==0.2.1',
        'python-multipart>=0.0.9,<1',
        'openpyxl>=3.1,<4',
        'ortools==9.12.4544',
    ],
    extras_require={
        'mcp': ['mcp>=1.10,<2'],
    },
    zip_safe=False,
    author="The unilabos developers",
    maintainer='Junhan Chang, Xuwznln',
    maintainer_email='Junhan Chang <changjh@pku.edu.cn>, Xuwznln <18435084+Xuwznln@users.noreply.github.com>',
    description='',
    license='GPL v3',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            "unilab = unilabos.app.main:main",
            "unilab-supervisor = unilabos.managed_runtime.supervisor:main",
            "unilab-mcp = unilabos.agent_tools.workflow:main",
        ],
    },
)

# =============================================================================
# 本地开发启动命令（前后端）
# -----------------------------------------------------------------------------
# 注意：本文件是 setuptools 的打包脚本，以下内容全部是注释，不参与构建。
#
# 一、后端（Workspace Backend + Edge，GN 工作站 dry-run 仿真）
#   终端 A：
#     export PATH="/Users/fenglongli/uni-lab-os/bin:$PATH"
#     export GN_PACKAGE="/Users/fenglongli/Downloads/Uni-Lab-OS/unilabos/devices/workstation/GN/gn-lab"
#     cd "$GN_PACKAGE"
#     unilab workspace start \
#       --workspace "$GN_PACKAGE" \
#       --component all \
#       --graph deployment/graphs/dry-run.json \
#       --runtime-mode dry-run \
#       --startup-mode develop \
#       --wait 300
#
#   真机（会真实驱动 510 的 OPC UA / PRCXI）：把 runtime-mode 换成 normal，
#   图换成 gn-station.json。action_mode 由 runtime-mode 决定（normal→real，
#   dry-run→simulate），是**进程级**启动参数，运行期不能按任务切换：
#     unilab workspace start \
#       --workspace "$GN_PACKAGE" --component all \
#       --graph deployment/graphs/gn-station.json \
#       --runtime-mode normal --startup-mode develop --wait 600
#
#   注意：必须**脱离沙箱**执行（ROS/CycloneDDS 需要枚举网卡，
#   沙箱里会报 `failed to enumerate interfaces for "udp"` 后退出）。
#
#   换图（尤其是 dry-run ↔ gn-station）时，若报「既有库存权威与资源图来源
#   或指纹冲突」，说明本地库存仍属于旧图。确认没有未完成的任务后用官方重置：
#     unilab workspace reset-local --workspace "$GN_PACKAGE" --yes \
#       --graph deployment/graphs/gn-station.json --runtime-mode normal --wait 300
#
#   查看状态 / 取后端地址（端口是动态的，每次重启会变）：
#     unilab workspace status --workspace "$GN_PACKAGE" --json
#     # 关键字段：components.backend.phase == "ready"
#     #           components.backend.address == "http://127.0.0.1:<port>"
#     # 读 GET {address}/api/v1/readiness 的 actionMode 确认当前是真机还是仿真档
#
#   停止：
#     unilab workspace stop --workspace "$GN_PACKAGE" --component all --json
#
#   查看后端日志：
#     unilab workspace logs --workspace "$GN_PACKAGE" --component backend --json
#
# 二、前端（React + Vite 开发服务器，Node >= 22.13）
#   终端 B（必须在 frontend/ 目录执行）：
#     export PATH="/usr/local/Homebrew/opt/node@22/bin:/Users/fenglongli/uni-lab-os/bin:$PATH"
#     export GN_PACKAGE="/Users/fenglongli/Downloads/Uni-Lab-OS/unilabos/devices/workstation/GN/gn-lab"
#     cd /Users/fenglongli/Downloads/Uni-Lab-OS/frontend
#     npm ci
#     BACKEND_URL=$(unilab workspace status --workspace "$GN_PACKAGE" --json \
#       | python3 -c "import json,sys; print(json.load(sys.stdin)['components']['backend']['address'])")
#     VITE_EDGE_API_URL="$BACKEND_URL" npm run dev -- --host 127.0.0.1 --port 4174
#
#   访问：http://127.0.0.1:4174/console/    （/api 会代理到 BACKEND_URL）
#
#   VITE_EDGE_API_URL 在启动时被写进前端 bundle，因此：
#   · 后端换端口后，**必须**用新 BACKEND_URL 重启 Vite，只刷新浏览器无效；
#   · 如果重启后页面仍显示「档位未知 / 尚未进入 Edge 列表」，说明 Vite
#     依赖缓存里仍是旧端口，清掉缓存再起：
#       rm -rf frontend/node_modules/.vite && npm run dev -- --host 127.0.0.1 --port 4174
#   （清缓存后首次启动要重新打包依赖，约 30~60 秒才开始监听端口。）
#
# 三、无需改前端源码时（直接用后端下发的静态控制台，已 npm run build）
#     http://127.0.0.1:<backend_port>/console/
#
# 四、前端生产构建（产物写入 Python 包静态目录）
#     cd /Users/fenglongli/Downloads/Uni-Lab-OS/frontend
#     npm run build      # -> ../unilabos/app/web/static/console/
#     npm test
#
# 五、后端重启后端口会变：需重新取 BACKEND_URL 并重启 Vite
#
# 六、工作流编辑器的「运行」
#    顶栏最右侧的动作档位指示器读 readiness.actionMode，如实显示「仿真档 /
#    真机档 / 档位未知」，不可在页面切换（进程级参数）。
#    · 仿真档：点「运行」直接提交 Task，驱动逻辑照跑但不打开 OPC 会话。
#    · 真机档：点「真机运行」先弹确认框（列明本次涉及的设备），确认后才提交；
#      授权只存页面内存，刷新即失效。页面顶部同时显示红色常驻横幅。
#    · 提交后在底部运行抽屉看：设备泳道甘特（同设备串行、异设备并行）、
#      并行度曲线、峰值并行/墙钟/加速比、状态跃迁事件；可暂停 / 继续 / 取消。
#    · 画布节点按运行态着色并显示状态徽标。
#    · 只有内容变更（节点/连线/参数）才要求先保存；纯排布（自动布局、拖动
#      节点）标记为「仅排布改动（可直接运行）」，不阻塞运行与预检。
# =============================================================================
