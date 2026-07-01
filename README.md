# TrafficMonitor Codex Usage Plugin

用于在 TrafficMonitor 中显示 Codex 的 5 小时额度和周额度；重置时间与今日 Token 明细放在鼠标悬停提示框中。

实现参考了 TrafficMonitor 官方插件开发指南和官方插件示例：插件 DLL 导出 `TMPluginGetInstance`，主程序周期性调用 `DataRequired()`，显示项通过 `IPluginItem` 返回文本。

## 显示项

- `5h`: Codex 300 分钟窗口剩余额度百分比，100% 为绿色，接近 0% 为红色。
- `周`: Codex 10080 分钟窗口剩余额度百分比，100% 为绿色，接近 0% 为红色。
鼠标移动到 TrafficMonitor 上时，提示框显示 5 小时/周剩余额度、重置时间，以及今日 `Input` / `Output` / `Cached` Token 明细；如果本机能查到 Codex 额度重置卡，也会追加可用数量、获取时间和过期时间。

双击任一显示项，或在插件命令里选择“立即刷新 Codex 用量”，会触发一次后台采集。

插件菜单还提供：

- 打开 Codex 用量 JSON
- 打开 Codex Usage 配置目录
- 打开 Codex Usage 诊断日志

“插件选项”会显示当前状态文件、日志文件、采集脚本路径，并可就近打开或复制这些路径；也可设置刷新时间间隔。刷新时间间隔单位为秒，允许范围是 `10-3600`，保存后会立即刷新一次。

## 数据来源

采集脚本只读本机 Codex 数据：

- `%USERPROFILE%\.codex\sessions\**\*.jsonl`
- `%USERPROFILE%\.codex\logs_2.sqlite`
- `%USERPROFILE%\.codex\sqlite\logs_2.sqlite`
- `%USERPROFILE%\.codex\state_5.sqlite`
- `%USERPROFILE%\.codex\sqlite\state_5.sqlite`

额度优先来自 session JSONL 中 `event_msg` / `token_count` 携带的 `rate_limits`；如果新格式不可用，再回退到日志中的 `codex.rate_limits` websocket 事件。`primary.window_minutes=300` 作为 5 小时额度，`secondary.window_minutes=10080` 作为周额度。若本地 payload 只提供 `used_percent`，采集脚本会换算出剩余百分比；快照中会同时保留 used 和 remaining 两种数值。

今日 Token 明细优先来自 `state_5.sqlite` 中记录的 rollout 路径，并读取 rollout JSONL 里的 `token_count.info.total_token_usage`，按本机当天增量统计 `Input`、`Output`、`Cached`。若 rollout 统计不可用，采集 JSON 中仍会保留旧日志/线程汇总字段作为兼容数据。

为了显示 Codex 额度重置卡，采集脚本会读取 `%USERPROFILE%\.codex\auth.json` 中的 `tokens.access_token`，并用 `Authorization: Bearer ...` 请求 `https://chatgpt.com/backend-api/wham/rate-limit-reset-credits`。状态 JSON 和 tooltip 只保留 `available_count` 以及每张卡的 `status`、`title`、本机时区下的获取时间和过期时间；不会写出 access token、refresh token、cookie 或完整唯一 ID。若 auth 缺失、401 或请求失败，则不显示重置卡区域。

## 构建

在仓库根目录运行：

```powershell
.\tools\build.ps1 -Platform x64 -Configuration Release
```

输出：

```text
build\x64\Release\CodexUsage.dll
build\x64\Release\scripts\
```

`tools\build.ps1` 会自动查找当前用户证书存储中的代码签名证书并签名 DLL。当前机器的 TrafficMonitor 进程受 Code Integrity 策略约束，未签名 DLL 会被系统拦截，表现为插件无法加载。若要跳过签名：

```powershell
.\tools\build.ps1 -Platform x64 -Configuration Release -SkipSign
```

也可以指定证书：

```powershell
$env:CODEX_TRAFFICMONITOR_SIGN_THUMBPRINT = '<code-signing-cert-thumbprint>'
.\tools\build.ps1 -Platform x64 -Configuration Release
```

也可以用 CMake 构建：

```powershell
cmake -S . -B build\cmake-x64 -A x64
cmake --build build\cmake-x64 --config Release
```

## 安装

把以下内容放到 TrafficMonitor 的插件目录中，并保持 `scripts` 文件夹与 DLL 同级：

```text
CodexUsage.dll
scripts\update_codex_usage.ps1
scripts\collect_codex_usage.py
```

重启 TrafficMonitor 后，在显示项目设置中启用 `Codex 5 小时额度`、`Codex 周额度`。重置时间不作为任务栏显示项提供，只显示在鼠标悬停提示框里。

## 手动采集

可以先手动生成一次状态 JSON：

```powershell
.\scripts\update_codex_usage.ps1
```

默认输出到：

```text
%USERPROFILE%\.codex\trafficmonitor\codex_usage_status.json
```

TrafficMonitor 插件运行时默认输出到：

```text
<TrafficMonitor 配置目录>\plugins\CodexUsage\codex_usage_status.json
<TrafficMonitor 配置目录>\plugins\CodexUsage\codex_usage_plugin.log
<TrafficMonitor 配置目录>\plugins\CodexUsage\codex_usage_plugin.ini
```

可用环境变量：

- `CODEX_TRAFFICMONITOR_USAGE_JSON`: 指定 DLL 读取和脚本写入的 JSON 路径。
- `CODEX_TRAFFICMONITOR_PYTHON`: 指定 `python.exe` 路径。
- `CODEX_HOME`: 指定 Codex 数据目录，默认 `%USERPROFILE%\.codex`。

## 说明

Codex 没有稳定公开的本地额度 API，因此该插件读取当前桌面端写入本地 session JSONL 或日志的 rate-limit 数据。如果最新额度事件超过 6 小时没有刷新，插件会显示 `旧`，表示额度百分比和重置时间可能已经过期。重置时间会继续显示上次记录值，并加上 `旧:` 前缀，例如 `旧: 5h 06-23 19:05 / 周 06-25 09:12`。

`update_codex_usage.ps1` 保持 ASCII 文本，避免 Windows PowerShell 5.1 将 UTF-8 无 BOM 脚本误按 ANSI 解码导致 ParserError。

## Release

推送 `v*` tag 会触发 GitHub Actions：

- 运行 Python 单元测试
- 构建 x64 Release DLL
- 打包 `CodexUsage.dll` 和 `scripts/`
- 将 `CodexUsage-TrafficMonitor-x64.zip` 上传到 GitHub Release

发布包默认未签名；如果你的 TrafficMonitor 环境要求代码签名，请用本机证书重新构建并签名。
