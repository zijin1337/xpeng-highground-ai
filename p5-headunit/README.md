# 高地 AI · 小鹏 P5 车端

这是可安装到小鹏 P5 车机 Android 环境的车端应用，不是网页动画或车辆运动模拟器。

## 已接入的真实能力

- 使用 Open-Xpeng 社区整理的 P5 XUI SDK `1.0.2` 编译。
- 监听 `ContextInfoManager.ContextNaviInfoEventListener` 的真实车速、原始挡位码和天气事件。
- 每 15 秒通过 HTTPS 调用现有后端的
  `GET /api/v1/decisions/latest`。
- 新风险事件到达时，使用 `SmartManager` 调用小 P 语音。
- 高风险事件使用 `AmbientLightManager` 临时切换柔和呼吸效果；风险解除或服务停止时恢复原有开关和效果。
- 前台服务持续运行；XUI 类、服务或权限不存在时，应用仍能启动并明确显示降级原因。
- 系统回收服务后保留车主的持续监控选择；任一目标 XUI 能力暂时不可用时每 60 秒重新探测，并在恢复后继续灯光提醒。
- 禁止 HTTP 重定向，避免 `X-API-Key` 被转发到其他地址；未知决策码、风险等级、异常时间、过长文本和越界传感器值会被拒绝，不会被误判为安全状态。

## 明确不包含

- 不挂挡、不转向、不制动、不调用自动驾驶或自动泊车。
- 不把社区 SDK 当作小鹏官方开放接口。
- 不推测 P5 原始挡位数字与 P/R/N/D 的对应关系，因此界面只显示原始值。
- P5 SDK 不提供雨量或积水传感器读数；这些数据仍由场端水位计/雨量计通过现有
  `backend/edge_client.py` 写入后端。

## 已核对的 SDK 来源

- 仓库：<https://github.com/open-xpeng/android_sdk>
- 使用版本：`1.0.2`
- 对应提交：`8b96a47929e508dc337f122d0dc2065c40e301e4`
- 社区文档标注车型/车机版本：小鹏 P5 / 3.6.1
- SDK 自述为“非公开安卓 SDK”，且承认并非全部 API 都经过测试。

依赖采用 `compileOnly`：APK 内不打包小鹏框架桩代码，运行时只使用 P5 Xmart OS
实际提供的 `com.xiaopeng.xuimanager` 类。

## 构建

要求：

- JDK 17 或更高版本
- Android SDK Platform 33
- Android SDK Build Tools
- 能访问 Google Maven、Maven Central 和 JitPack

在本目录运行：

```powershell
.\gradlew.bat :app:testDebugUnitTest :app:assembleDebug :app:lintDebug
```

调试 APK：

```text
app/build/outputs/apk/debug/app-debug.apk
```

也可以在 GitHub 对应提交或 PR 的 Actions 运行中下载 `highground-p5-debug-apk`
构建产物。该文件是未使用小鹏系统签名的调试包，只用于授权测试设备。

安装到已由车主或开发单位合法开启 ADB 的 P5：

```powershell
adb install -r .\app\build\outputs\apk\debug\app-debug.apk
```

是否允许第三方 APK、是否授予三个 XUI 权限，取决于具体 P5 的 Xmart OS 版本、系统签名和白名单。
社区资料没有提供可验证的量产授权流程，所以应用会做运行时能力探测，不会声称“编译成功”等于“已获车端权限”。

## 配置与联调

1. 部署仓库现有 FastAPI 后端，并让 P5 能通过网络访问。
2. 场端水位计/雨量计继续向 `/api/v1/telemetry` 写入遥测。
3. 在 P5 应用中填写后端地址、`X-API-Key`、场站 ID 和车辆 ID。
4. 点击“保存并启动”。页面会分别显示后端、P5 XUI、车速/挡位/天气和最新决策状态。
5. 在安全静止状态下，由测试人员主动点击“小 P 测试”和“灯光测试”验证权限。

正式版禁止明文 HTTP。调试版为了封闭局域网联调允许 HTTP，并在启动时明确提示。

## 上车前验证清单

- P5 车机版本与 SDK 目标版本匹配，并记录完整版本号。
- 在车辆静止、P 挡、封闭环境中验证 XUI 服务和权限。
- 核对车速回调的单位；核对原始挡位码，但在未获得正式枚举前不得映射。
- 验证语音优先级及环境灯恢复行为。
- 断网、后端 401/404/500、XUI 服务重启时应用不崩溃。
- 验证后端 3xx 重定向、未知决策码和越界传感器数据不会触发错误的风险解除。
- 禁止在公开道路把该应用当作驾驶控制系统。
