# 可复现服务合同 Benchmark

本 Benchmark 用固定 JSON 场景矩阵验证默认单车策略与六车 Fleet Shadow 规划，并测量本地进程内 API 路径的延迟。它回答两个问题：给定同一组输入，决策是否仍符合合同；在当前机器的临时 SQLite 与 FastAPI `TestClient` 中，一次请求用了多长时间。

它不是 P5 泊车或涉水性能 Benchmark。仓库没有同配置实车、统一场地和统一测量协议，因而不报告车辆迁移速度、泊车成功率或安全涉水深度；与小鹏公开能力的对齐只作为[定性证据矩阵](./competition-comparison.md)，未知项明确标为“未核验”。

## 测量边界

- `benchmarks/scenarios.json` 固化默认策略、基础遥测、场景差异和预期结果。
- `demo/scenarios/fleet-rainstorm-v1.json` 固化 6 辆车、6 个暴雨阶段，以及逐车排序、容量分配、窗口关闭和安全拒绝的预期投影。
- 每个场景先做一次不计时的正确性检查；任何状态码、决策、风险等级、权限或失败安全闸不符都会使运行失败。
- Fleet Shadow 先对全部 6 个阶段做不计时正确性检查，再对第 4 阶段执行指定次数的计时 POST；每次使用新的 `snapshot_id`、消息 ID 和当前时间戳。
- 测量包含进程内 ASGI 分派、Pydantic 校验、决策计算、SQLite 事务与响应序列化。
- 测量不包含网络、TLS、反向代理、场端网关、传感器采样、P5/XUI 轮询或车辆执行。
- `record_only_command` 只测授权令牌消费与命令留痕，期望结果始终是 `RECORDED_NOT_SENT`，没有向车辆发令。
- `fleet_shadow_run` 只测影子规划与 SQLite 证据写入；全部逐车结果保持 `authorized_to_move=false`，Fleet 路径不写授权或命令表。
- p50/p95 使用最近秩法：将样本升序排列，取第 `ceil(p * N)` 个样本。

这些数字只描述运行报告所列机器、Python 版本、依赖版本与本地 `TestClient`。它们不是生产 SLO，也不能用来推导小鹏官方功能或其他产品的性能结论；跨机器、跨部署或跨产品比较需要相同硬件、网络、数据规模和测量协议。

## 场景矩阵

| 场景 | 关键变化 | 预期决策 | 风险 | 权限 |
|---|---|---|---|---|
| `stay_clear` | 低雨量、长安全窗口 | `STAY` | `LOW` | `NONE` |
| `watch_heavy_rain` | 雨量 65 mm/h | `WATCH` | `MEDIUM` | `NONE` |
| `prepare_window` | 最晚启动窗口进入准备区间 | `PREPARE` | `MEDIUM` | `AWAITING_OWNER` |
| `migrate_window` | 最晚启动窗口为正且不超过 7 分钟 | `MIGRATE_NOW` | `HIGH` | `AWAITING_OWNER` |
| `verify_low_confidence` | 传感器置信度低于 0.72 | `VERIFY_ONLY` | `LOW` | `DENIED` |
| `no_go_route_blocked` | 出口/路线阻塞 | `NO_GO` | `HIGH` | `DENIED` |
| `no_go_water_threshold` | 水位达到 22 cm 禁行阈值 | `NO_GO` | `CRITICAL` | `DENIED` |
| `no_go_window_closed` | 最晚安全启动窗口已经关闭 | `NO_GO` | `CRITICAL` | `DENIED` |
| `emergency_stop_link_loss` | 移动中网络离线 | `EMERGENCY_STOP` | `HIGH` | `DENIED` |

完整输入和断言以 `benchmarks/scenarios.json` 为准，表格仅用于快速阅读。

## Fleet Shadow 场景

Fleet 部分使用与网页和 Python Planner 相同的 `fleet-rainstorm-v1.json`：日常守望、强降雨、窗口收窄、快速上涨、容量受限、窗口关闭。每阶段固定 6 辆模拟车辆；正确性检查覆盖 `SCHEDULED_SHADOW`、`NO_CAPACITY`、`WINDOW_CLOSED`、`VERIFY_ONLY` 和路线拒绝等结果。计时样本选择第 4 阶段，因为它同时包含 2 个影子排程、容量拒绝、低置信度复核和路线拒绝。

报告新增以下独立区块：

```json
{
  "fleet_shadow": {
    "correctness": {
      "passed": true,
      "stage_count": 6,
      "vehicle_count_per_stage": 6,
      "vehicle_command_transmitted": false
    },
    "latency": {
      "fleet_shadow_run": {
        "count": 50,
        "min_ms": 0,
        "p50_ms": 0,
        "p95_ms": 0,
        "max_ms": 0,
        "mean_ms": 0
      }
    }
  }
}
```

示例中的零只说明字段类型，真实值由当前运行的最近秩统计生成。这里没有毫秒通过阈值，也不构成生产 SLO。

## 运行

先安装后端开发依赖，然后从仓库根目录执行：

```powershell
python -m pip install -r backend/requirements-dev.txt
python -m benchmarks.run_benchmark --iterations 50 --warmups 3
```

保存 JSON 报告：

```powershell
python -m benchmarks.run_benchmark `
  --iterations 100 `
  --warmups 5 `
  --output benchmark-result.json
```

报告同时记录场景数、策略、百分位算法、操作系统、Python、SQLite、FastAPI、Starlette 与 `httpx2` 版本。临时数据库在每次运行时重新创建，单车 `message_id` 以及 Fleet `snapshot_id`/消息 ID 按阶段、阶段类型和迭代次数生成，因此旧数据不会污染结果。仓库中的 [`benchmarks/results/reference-local.json`](../benchmarks/results/reference-local.json) 仍是 v1.2 单车报告快照；它不包含 v1.3 Fleet 区块，也不是固定性能基线。运行上面的命令可生成当前完整结构。

## CI 正确性检查

常规测试不会设置毫秒阈值，避免把共享 CI 机器的负载抖动误判成产品回归。它只验证单车矩阵覆盖全部决策代码、Fleet 六阶段全部通过、未发送车辆命令、样本数量正确以及百分位顺序有效：

```powershell
python -m pytest backend/tests/test_benchmark.py -q
```

需要比较两个提交时，应在同一台空闲机器上固定 Python 与依赖版本，分别运行多轮并保留原始 JSON；不要将单次 p50/p95 差异直接解释为跨产品性能优势。

Fleet 数据同样完全来自仓库模拟 JSON。Benchmark 不含网络、真实停车场、P5/XUI、传感器、硬件执行或训练模型，不应与小鹏官方产品指标作数值比较。
