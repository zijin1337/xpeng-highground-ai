# 两分钟暴雨全流程 Demo

本 Demo 通过本地 FastAPI、SQLite、授权和命令 API 运行固定的 120 秒场景。车辆适配器保持 `record-only`，最终状态必须为 `RECORDED_NOT_SENT`；路线动画只是数字沙盘回放，不代表车辆移动或车机验证。

## 一键运行

先安装仓库开发依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
```

正式两分钟流程：

```powershell
.\demo\run_demo.ps1 -TimeScale 1
```

快速回归使用同一场景和全部断言，但跳过等待：

```powershell
.\demo\run_demo.ps1 -TimeScale 0
```

脚本会启动仅监听 `127.0.0.1` 的临时服务，等待 `/healthz` 确认 `record-only`，运行场景，然后停止服务。证据默认写到 `demo/artifacts/latest-evidence.json`，该目录已被 Git 忽略。

## 固定时间线

| 时间 | 操作 | 必须通过的断言 |
|---:|---|---|
| `00:00` | 提交日常遥测 | `STAY / LOW / NONE` |
| `00:20` | 提交强降雨遥测 | `WATCH / MEDIUM / NONE` |
| `00:45` | 提交窗口收窄遥测 | `PREPARE / MEDIUM / AWAITING_OWNER` |
| `01:10` | 提交快速上涨遥测 | `MIGRATE_NOW / HIGH / AWAITING_OWNER` |
| `01:25` | 为当前事件签发单次授权 | HTTP `201` |
| `01:30` | 执行前重算并提交命令 | `RECORDED_NOT_SENT / record-only` |
| `01:45` | 查询审计事件 | 至少 4 条事件 |
| `01:55` | 水位到达禁行阈值 | `NO_GO / CRITICAL / DENIED` |
| `02:00` | 查询最新决策 | 仍为 `NO_GO / CRITICAL / DENIED` |

清单源文件是 [`demo/scenarios/rainstorm-p5-120s.json`](../demo/scenarios/rainstorm-p5-120s.json)。`demo/run_scenario.py` 对每一步的 HTTP 状态、决策、风险级别、权限和适配器结果执行断言，任一项不符即以非零状态退出。

## 录屏口径

录制时同时展示运行终端和 `http://127.0.0.1:8000/`。终端使用 `-TimeScale 1` 跑满两分钟；网页连接本地后端后可展示当前事件及 SQLite 留痕。路线回放按钮只有收到 `RECORDED_NOT_SENT` 响应后才解锁，并持续标注“数字演示 / 未向车辆发令”。

已核验的 120 秒 MP4、manifest 和脱敏 evidence 发布在 [v1.2.0 GitHub Release](https://github.com/zijin1337/xpeng-highground-ai/releases/tag/v1.2.0)。视频是本地 HTTP/SQLite 运行记录，不是 P5 实车试验录像。

## 生成本地 120 秒成片

视频脚本以 Web 控制台状态截图作为界面参考，并按已断言的 HTTP evidence 驱动时间轴、状态码、延迟、决策和传感器值。状态截图可能来自另一次本地运行，不作为本轮安全证据；manifest 会记录每张截图的 SHA-256，并标记 `captures_are_evidence=false`。脚本会拒绝渲染未通过断言、不是 `record-only`、车型档案或 `/healthz` 预检不匹配、声称已向车辆发令或任意层级包含明文授权令牌的 evidence。

先跑满两分钟并保存本次 evidence：

```powershell
.\demo\run_demo.ps1 -TimeScale 1 -Output demo/artifacts/video-evidence-120s.json
```

安装独立的视频依赖并生成 `1920x1080`、H.264、约 120 秒的 MP4：

```powershell
.\.venv\Scripts\python.exe -m pip install -r demo\requirements-video.txt
.\.venv\Scripts\python.exe demo\render_video.py `
  --evidence demo\artifacts\video-evidence-120s.json `
  --output demo\artifacts\rainstorm-p5-120s.mp4
```

脚本默认使用仓库中的 Web 控制台截图 `assets/highground-demo.png`。录制多个状态后，可把截图放进 `demo/artifacts/video-captures/`；脚本会按时间点自动选用存在的图片：`stay.png`、`watch.png`、`prepare.png`、`migrate.png`、`authorized.png`、`recorded.png`、`nogo.png`。缺少的状态会回退到相邻状态截图或默认截图；不会根据 evidence 伪造网页状态，也不会把截图里的旧事件号当成本轮证据。

渲染结束后，同目录会生成 `rainstorm-p5-120s.manifest.json`，记录实际编码、像素格式、帧率、时长、分辨率、帧数、文件大小、MP4 SHA-256、evidence SHA-256、每张截图的 SHA-256，以及动态字段与截图各自的证据角色。MP4、manifest、运行 evidence 和状态截图均位于已被 Git 忽略的 `demo/artifacts/`；发布前应以 manifest 校验本地产物，公开副本见 [v1.2.0 Release](https://github.com/zijin1337/xpeng-highground-ai/releases/tag/v1.2.0)。

## 证据与隐私

证据 JSON 是本地 FastAPI/SQLite 运行记录，包含运行环境、各步骤请求路径、延迟、期望值、响应、事件/消息/授权/命令 ID 关联和断言结果。渲染前会按后端 canonical JSON 规则从持久化 telemetry 重新计算 `input_sha256`，并校验它在遥测、事件查询和最新决策之间一致；只把证据文件中重复出现的哈希统一改写也会被拒绝。明文单次授权令牌不会写入文件，只保留 SHA-256；API Key 也不会写入报告。报告明确记录：

```json
{
  "record_only": true,
  "vehicle_command_transmitted": false
}
```

该记录与 manifest 哈希可用于检查发布后文件是否被改动，但没有第三方签名、可信时间戳或硬件远程证明，因此不把它表述为传感器或实车来源的独立认证。当前可证明的是本地 HTTP/SQLite 流程按固定场景运行并通过断言；实车与现场传感器仍须另行验证。

## 自动验证

```powershell
.\.venv\Scripts\python.exe -m pytest `
  backend\tests\test_competition_demo.py `
  backend\tests\test_competition_demo_launcher.py `
  backend\tests\test_demo_video.py `
  -q
```

`test_competition_demo.py` 通过 FastAPI `TestClient` 和临时 SQLite 跑完整链路；`test_competition_demo_launcher.py` 验证一键脚本的进程、健康预检与参数边界；`test_demo_video.py` 验证成片输入必须来自完整 120 秒、`record-only`、无明文令牌且未向车辆发令的 evidence，并校验遥测、事件列表、授权、命令和最新决策的 ID/哈希关联。三组测试共同覆盖固定时间点、决策序列、授权令牌脱敏与 `RECORDED_NOT_SENT` 结果。
