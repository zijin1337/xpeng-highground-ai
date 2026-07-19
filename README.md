# 高地 AI · XPENG P5 暴雨风险控制台

面向地下车库暴雨内涝的可运行安全决策原型：场端接收遥测、计算 Go / No-Go、保存证据与授权记录；P5 车端提供只读状态和座舱提醒。

![高地 AI 暴雨风险控制台](./assets/highground-demo.png)

> 重要边界：后端、数据库、API、授权和命令留痕均可真实运行；默认车辆适配器为 `record-only`，不会向任何真实车辆发送控制指令。接入实车必须取得制造商官方 SDK/API、车辆授权、封闭场地许可，并完成独立安全壳和功能安全验证。本仓库不伪造小鹏车辆控制接口。

## 评审证据入口

- [系统架构图](#系统结构)：场端、浏览器、FastAPI/SQLite 证据链与 P5 只读端的 Mermaid 数据流，并明确画出当前不存在的车辆执行链路。
- [两分钟暴雨全流程 Demo](./docs/DEMO.md)：固定 120 秒时间线、一键脚本、逐步断言、带事件链 ID 与 SHA-256 的脱敏 HTTP 运行记录和录屏清单；当前尚未发布公开视频。
- [可复现 Benchmark](./docs/BENCHMARK.md)：9 个确定性场景覆盖全部 7 个决策码，并报告限定在本机 TestClient + 临时 SQLite 的 p50/p95。
- [现有方案与小鹏能力对比](./docs/competition-comparison.md)：地磁、人工通知、传统闸门、泊车能力、涉水边界及官方资料来源。
- [传感器与边缘网关成本模型](./docs/cost-model.md)：内部预算假设下，单站 3 年 TCO 为 `¥77,200`、10 站为 `¥924,000`；逐项 BOM、算式、未计价项和正式 RFQ 条件均可复核。

## 现在真正可运行的部分

- `POST /api/v1/telemetry` 接收 HTTP 传感器与车辆状态遥测。
- Pydantic 在入口校验范围、类型、时区和标识符，非法输入返回 `422`。
- SQLite 使用 WAL、外键和事务保存原始输入、SHA-256、决策、授权和命令记录。
- `message_id` 提供内容校验后的幂等写入；相同载荷重试不会重复产生事件，不同载荷复用同一 ID 会返回 `409`。
- 服务端策略独立于传感器输入，终端不能自行修改禁行阈值。
- 决策顺序固定为：移动中异常 → 物理安全闸 → 水位禁行 → 多源一致性 → 最晚安全启动窗口。
- `MIGRATE_NOW` 事件可签发短时、事件绑定、只能使用一次的授权令牌。
- 命令执行前再次用原始遥测做安全计算，并拒绝过期、已被同场站同车辆新遥测取代的事件和复用令牌。
- 最新决策超过 `HIGHGROUND_EVENT_MAX_AGE_SECONDS` 后返回 `410 Gone`，不会把历史结果继续伪装成实时状态。
- `record-only` 适配器会将通过校验的命令写入数据库，但明确标记 `RECORDED_NOT_SENT`。
- 浏览器可输入 API Key 连接同源后端；“运行决策”会把遥测写入本地 SQLite，并显示服务端事件号和 SHA-256。
- 对 `MIGRATE_NOW` 事件，浏览器可在车主勾选单次授权后调用后端授权与命令 API；默认结果为 `RECORDED_NOT_SENT`，命令已写库但未发送任何车辆控制指令。
- 浏览器路线动画只在收到 `RECORDED_NOT_SENT + record-only` 后解锁，并始终标注为数字路线演示；它不表示实车已经移动。
- P5 Android 应用只轮询最新决策。XUI 车速、挡位和天气状态只在车机本地用于展示与提醒，不作为场端遥测上传。
- 提供边缘采集客户端、Docker、健康检查、OpenAPI 文档以及前后端自动测试。

## 系统结构

```mermaid
flowchart TB
    subgraph SITE["地下车库场端"]
        S["主/辅水位计、雨量计、路线与闸机状态"] --> GW["edge_client / 场端网关"]
    end

    subgraph WEB["浏览器控制台"]
        LOCAL["GitHub Pages / 本地模式"] --> JS["浏览器解释型决策<br/>不写 SQLite、不签发授权"]
        APIUI["API 连接模式"]
        DIGITAL["数字路线演示<br/>仅回放 RECORDED_NOT_SENT"]
    end

    subgraph BACKEND["FastAPI 决策与证据服务"]
        TAPI["POST /telemetry"] --> VALIDATE["Pydantic 校验与幂等检查"]
        VALIDATE --> ENGINE["确定性安全决策引擎"]
        ENGINE --> EVIDENCE["遥测 + SHA-256 + 决策事务"]
        AUTH["POST /authorizations"] --> FRESH["最新事件、新鲜度、MIGRATE_NOW 校验"]
        CMD["POST /commands/migrate"] --> RECHECK["执行前重新计算安全条件"]
        RECHECK --> RO["RecordOnlyActuator"]
        RO --> ATOMIC["原子事务：校验最新事件<br/>消费单次令牌 + 写命令留痕"]
        RO --> NOSEND["RECORDED_NOT_SENT<br/>不发送车辆控制指令"]
        LATEST["GET /decisions/latest"]
        DB[("SQLite 证据库")]
        EVIDENCE --> DB
        FRESH --> DB
        ATOMIC --> DB
        DB --> LATEST
    end

    subgraph P5["XPENG P5 Android 只读监控"]
        APP["HighGroundMonitorService"] -->|"只读轮询"| LATEST
        XUISTATE["XUI 本地车速、挡位、天气"] --> APP
        APP --> REMIND["小 P 语音 / 环境灯提醒"]
    end

    GW -->|"HTTPS + X-API-Key"| TAPI
    APIUI -->|"遥测"| TAPI
    APIUI -->|"事件级授权"| AUTH
    APIUI -->|"留痕命令"| CMD
    NOSEND --> DIGITAL
```

图中没有从 P5/XUI 指向 `/telemetry` 的连线，也没有从 `RecordOnlyActuator` 指向车辆执行器的连线。这两条缺失是当前实现的明确边界，不是省略。

## 方式一：Docker 启动

### Windows 双击启动

确保 Docker Desktop 已运行，然后双击仓库根目录的 `start-highground.cmd`。脚本会构建并启动服务、等待健康检查通过，再自动打开浏览器。首次本地演示可在页面输入：

```text
X-API-Key: change-this-before-deploy
```

需要停止时，双击 `stop-highground.cmd`。

### 命令行启动

复制环境变量模板，并务必修改 API Key：

```bash
cp .env.example .env
```

启动：

```bash
docker compose --env-file .env up --build
```

打开：

- 操作界面：`http://127.0.0.1:8000/`
- OpenAPI/Swagger：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/healthz`

在界面的“后端 X-API-Key”中填写 `.env` 里的 `HIGHGROUND_API_KEY`，点击“连接本地后端”。连接成功后，“运行决策”会写入本地 SQLite，而不是只运行浏览器内算法。

要验证完整后端安全链路：选择“暴雨快速上涨” → 点击“运行决策” → 勾选“车主确认本次单次授权” → 点击“验证一次性授权并记录命令”。页面会调用两个本地 API、重新执行安全校验，并显示“命令已写入本地 SQLite · 未发送车辆”。

停止：

```bash
docker compose down
```

数据库默认持久化到 `./data/highground.db`。

## 方式二：本地 Python 启动

```bash
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements-dev.txt
$env:HIGHGROUND_API_KEY = "replace-with-a-long-random-value"
$env:HIGHGROUND_ACTUATOR_MODE = "record-only"
uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
```

macOS/Linux：

```bash
source .venv/bin/activate
pip install -r backend/requirements-dev.txt
export HIGHGROUND_API_KEY="replace-with-a-long-random-value"
export HIGHGROUND_ACTUATOR_MODE="record-only"
uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
```

## 两分钟暴雨 Demo

Windows 下从仓库根目录运行：

```powershell
.\demo\run_demo.ps1 -TimeScale 1
```

脚本会启动本地 FastAPI，按 `0 / 20 / 45 / 70 / 85 / 90 / 105 / 115 / 120` 秒执行遥测、授权、命令留痕和证据查询，并断言最终为 `NO_GO`。快速回归可用 `-TimeScale 0`。完整时间线、录屏口径与证据字段见 [Demo 文档](./docs/DEMO.md)。当前仓库提供可复现录屏流程，但尚未发布 MP4、GitHub Release 或 B 站成片链接。

## 发送一条本地 HTTP 遥测

仓库自带的边缘客户端使用 Python 标准库，不需要额外依赖：

```bash
python backend/edge_client.py \
  --api-url http://127.0.0.1:8000 \
  --api-key replace-with-a-long-random-value \
  --file backend/examples/rising-water.json
```

连续发送 10 个样本，并让水位每次增加 `0.4 cm`：

```bash
python backend/edge_client.py \
  --api-key replace-with-a-long-random-value \
  --repeat 10 \
  --interval 2 \
  --rise-per-sample 0.4
```

真实水位计或雨量计只需将自身读数映射到同一 JSON 契约，再通过 HTTPS 调用遥测接口。示例载荷位于 `backend/examples/rising-water.json`。

## 核心 API

| 方法 | 路径 | 作用 | 是否需要 API Key |
|---|---|---|---|
| `GET` | `/healthz` | 服务和数据库健康检查 | 否 |
| `GET` | `/api/v1/policy` | 查看服务端安全策略 | 否 |
| `GET` | `/api/v1/session` | 验证 API Key 与运行模式 | 是 |
| `POST` | `/api/v1/telemetry` | 写入遥测并生成决策 | 是 |
| `GET` | `/api/v1/decisions/latest` | 查询车辆最新决策 | 是 |
| `GET` | `/api/v1/events` | 查询车辆事件历史 | 是 |
| `GET` | `/api/v1/events/{event_id}` | 获取完整遥测与证据链 | 是 |
| `POST` | `/api/v1/authorizations` | 为可迁移事件签发一次性授权 | 是 |
| `POST` | `/api/v1/commands/migrate` | 重新校验并记录迁移命令 | 是 |

请求头：

```text
X-API-Key: replace-with-a-long-random-value
```

完整字段、示例和可交互请求见 `/docs`。

## 决策模型

时间窗口：

```text
T_last = T_threshold - T_route - T_queue - T_buffer
```

- `T_threshold`：当前水位按上涨速度到达禁行阈值的剩余时间。
- `T_route`：按封闭场地 `≤5 km/h` 估算的干燥路线时间。
- `T_queue`：多车分批放行的排队时间。
- `T_buffer`：定位、闸机、路径和通信的不确定性余量。

只要路线见水、出口受阻、车内有人、充电未断开、车辆故障、定位/通信/人工兜底失效或水触触发，结果均为 No-Go。安全闸优先级高于时间窗口和车主授权。

## 授权与命令安全

1. 只有决策为 `MIGRATE_NOW`、仍在新鲜度窗口内且仍是同场站同车辆最新结果的事件才能申请授权。
2. 授权令牌使用安全随机数生成，数据库只保存 SHA-256。
3. 令牌绑定单个事件、短时过期、只能消费一次。
4. 命令前重新计算安全条件并检查事件新鲜度；事务内再次检查最新事件身份。
5. 消费令牌与写入 `record-only` 命令记录在同一 SQLite 事务中完成，任一步失败都会回滚。
6. 默认适配器只记录命令，不向车辆发送任何数据。
7. `HIGHGROUND_ACTUATOR_MODE=disabled` 可完全关闭命令入口。

生产部署还必须增加 TLS、密钥托管、设备证书或 mTLS、细粒度身份授权、速率限制、集中日志、备份和数据库迁移。MVP 的 API Key 机制不应直接视为量产认证方案。

## 自动测试

后端测试覆盖：

- API Key 拒绝与认证会话；
- 正常遥测写入、最新决策查询与陈旧结果 `410`；
- `message_id` 相同载荷幂等与不同载荷冲突；
- 传感器冲突和关闭的安全窗口；
- No-Go 不可授权；
- `MIGRATE_NOW` → 一次性授权 → 命令留痕；
- 已被新遥测取代的事件不能授权或记录命令；
- 授权令牌不能重复使用，命令写库失败不会提前消费令牌；
- SQLite 请求连接会在操作后显式关闭；
- SQLite 事件历史持久化。
- 两分钟固定 Demo 的完整 HTTP、授权、`RECORDED_NOT_SENT` 和令牌脱敏流程；
- 9 场景 Benchmark 矩阵对全部决策码的合同覆盖与百分位报告结构。

运行全部测试：

```bash
npm test
python -m pytest backend/tests -q
python -m benchmarks.run_benchmark --iterations 50 --warmups 3
```

GitHub Actions 会同时运行 JavaScript 决策测试和 Python API/数据库测试。

## 项目结构

```text
xpeng-highground-ai/
├─ backend/
│  ├─ app/
│  │  ├─ main.py               # FastAPI、认证、API 和静态页面
│  │  ├─ decision_engine.py    # 服务端确定性安全引擎
│  │  ├─ database.py           # SQLite 事务、幂等、证据与授权
│  │  ├─ actuator.py           # 默认不发送车辆指令的安全适配层
│  │  ├─ models.py             # Pydantic 输入输出契约
│  │  └─ config.py             # 服务端策略与环境配置
│  ├─ edge_client.py           # 传感器/网关 HTTP 客户端
│  ├─ examples/                # 可发送的遥测样本
│  └─ tests/                   # API、数据库与引擎测试
├─ src/
│  ├─ app.js                   # UI、API 连接和本地降级演示
│  └─ decision-engine.js       # 浏览器内解释型引擎
├─ demo/                       # 固定两分钟场景、一键脚本与 HTTP 断言运行器
├─ benchmarks/                 # 确定性场景矩阵、本地延迟工具与参考报告
├─ docs/                       # Demo、Benchmark、竞品对比与成本模型
├─ p5-headunit/                # 面向 P5 XUI 的 Android 只读监控与座舱提醒工程
├─ Dockerfile
├─ docker-compose.yml
├─ index.html
└─ styles.css
```

## GitHub Pages 与可运行后端的区别

GitHub Pages 地址只能运行静态界面和浏览器演示，因为 Pages 不能运行 Python 或 SQLite。要使用后端遥测 API、数据库、授权和命令接口，必须用 Docker/本地 Python 启动本仓库，或把容器部署到支持后端服务的平台。

## 小鹏 P5 车端

[`p5-headunit/`](./p5-headunit/) 是独立 Android 工程，按 Open-Xpeng 社区 P5 XUI SDK
`1.0.2` 的类定义编译。工程实现了监听车速、原始挡位码和天气事件、轮询本后端最新决策，以及调用小 P 语音和环境灯提醒的代码路径。最新决策过期时会清除陈旧卡片并等待新遥测，但不会把“数据未知”当成风险解除；风险灯意图会本地锁存，并在持续监控服务重建后尝试恢复。XUI 运行时或系统权限不可用时会明确降级并定期重新探测，不会崩溃或伪造成功。
车端拒绝后端重定向和未知决策码，避免 API Key 泄露或把不兼容响应误判为安全状态。
车速、挡位和天气事件不离开 Android 应用，也不会被该工程 POST 到场端遥测接口。当前只能确认 JVM 单元测试、APK 构建和 Android Lint 已通过；尚未完成 P5 实车/Xmart OS 白名单验证，因此不把上述代码路径表述为已在车机运行。

```powershell
cd p5-headunit
.\gradlew.bat :app:testDebugUnitTest :app:assembleDebug :app:lintDebug
```

完整构建、安装、联调和上车验证说明见 [`p5-headunit/README.md`](./p5-headunit/README.md)。

## 车辆自动移动接入还缺什么

仓库故意没有虚构“小鹏车辆移动 API”。真正接车至少需要：

1. 小鹏或车辆制造商正式授权的 SDK/API、证书和车辆绑定流程；
2. 可审计的车辆状态、充电互锁、乘员检测、定位、远程急停接口；
3. 认证封闭 ODD、干燥路线、高位点和场端闸机协议；
4. 独立安全壳，而不是由风险模型直接控制执行器；
5. HIL、封闭场地、故障注入、回归和功能安全验证；
6. 保险、隐私、网络安全、数据留存和事故责任流程。

拿到官方接口规范后，仍需实现 `VehicleActuator` 协议，并完成上述授权、互锁、安全壳、HIL 和封闭场地验证；全部验收通过后才可评估替换 `RecordOnlyActuator`。在此之前，系统会明确拒绝声称已控制真实车辆。

## 许可证

代码以 [MIT License](./LICENSE) 发布。开源许可证不免除任何车辆安全、法规、隐私和授权责任。
