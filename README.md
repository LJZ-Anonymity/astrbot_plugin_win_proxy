# astrbot_plugin_win_proxy

Windows-MCP 服务托管插件 / Windows-MCP service manager for AstrBot

让 AstrBot 在加载时自动拉起本机的 [Windows-MCP](https://github.com/CursorTouch/Windows-MCP) 服务，并附带一个桌面操作 Skill，方便 Agent 通过 MCP 查看和操作本机 Windows 桌面。

> [!WARNING]
> 这个插件会让 AI 有能力查看并操作你的电脑桌面。请只在自己信任的环境和人格下使用，详见下方「安全提示」。

## 工作方式

整套能力由三部分配合完成，缺一不可：

| 部分 | 作用 |
| --- | --- |
| 本插件 | 自动启动 / 停止本地 Windows-MCP 服务（默认监听 `127.0.0.1:8000`） |
| AstrBot 的 MCP 配置 | 把服务提供的工具登记给 AstrBot，Agent 才能调用 |
| 插件自带的 Skill | 告诉 Agent 何时、如何使用桌面工具（先看后做、危险操作先确认） |

插件本身不提供任何聊天指令，也不会在对话中主动回复。

## 功能

- 插件加载时按配置自动启动 Windows-MCP，在后台等待就绪，不阻塞 AstrBot 启动
- 插件卸载或重载时自动清理子进程
- 通过 `uvx` 运行，指定 Python 版本、PyPI 镜像源，并可调整下载超时与重试次数
- 服务输出统一转发到 AstrBot 日志，前缀为 `[windows-mcp]`
- 随插件提供 `windows-mcp` Skill，在 WebUI 的 `插件 → 技能` 中显示

## 环境要求

- Windows（停止服务时会调用 PowerShell 清理端口占用）
- AstrBot `>=4.16,<5`
- 已安装 [uv](https://docs.astral.sh/uv/)，并知道 `uvx.exe` 的路径
- 能访问所配置的 PyPI 镜像源

## 安装

1. 将本插件目录放入 AstrBot 的 `data/plugins/` 下，或在 WebUI 的插件页面安装。
2. 在插件配置页填写 `uvx_path` 等配置（见下表）。
3. 重载插件。若开启了 `auto_start`，会自动开始启动服务。

**首次启动需要下载并构建依赖，可能要几分钟，期间请不要重载插件**，否则子进程会被清理，需要从头来过。依赖进入 uv 缓存后，之后的启动会很快。

## 配置项

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `uvx_path` | 空 | `uvx.exe` 的路径，请改成你自己的 |
| `python_version` | `3.14` | 运行 Windows-MCP 的 Python 版本 |
| `uv_python_dir` | 空 | uv 托管 Python 的安装目录，**必须是目录**（例如 `C:\Users\<用户名>\AppData\Roaming\uv\python`），不是 `python.exe`。填写后会禁止 uv 自动下载 Python；留空则使用默认环境 |
| `index_url` | `https://mirrors.aliyun.com/pypi/simple/` | PyPI 镜像源 |
| `http_timeout` | `300` | uv 下载超时（秒），网络慢时调大 |
| `http_retries` | `5` | uv 下载失败的重试次数 |
| `package_spec` | `windows-mcp==0.9.0` | Windows-MCP 的 PyPI 包名与版本，留空则安装最新版 |
| `host` | `127.0.0.1` | 监听地址 |
| `port` | `8000` | 监听端口 |
| `auto_start` | `true` | 加载插件时自动启动 Windows-MCP |

## 接入 AstrBot

插件只负责把服务启动起来，还需要下面几步，Agent 才能真正使用桌面工具：

1. **添加 MCP 服务器**：在 WebUI 的 `插件 → MCP` 中新增一项，类型选 Streamable HTTP，地址填 `http://127.0.0.1:8000/mcp`（与 `host`、`port` 保持一致）。添加成功后应显示「已连接」，并能看到可用工具列表。
2. **开启电脑能力**：在 `配置文件 → AI → 能力 → 使用电脑能力` 中选择 `local`，否则 Agent 无法读取 `SKILL.md`。
3. **选择技能**：在 `人格设定` 中编辑要使用的人格，在人格能力里勾选 `windows-mcp` 的 MCP 工具和技能。默认人格使用全部能力，如需限定，请使用自定义人格。

如果之后 MCP 页面显示未连接（比如 AstrBot 先于服务启动），等服务就绪后点页面右下角的刷新按钮重新连接。

## Skill 说明

Skill 位于 `skills/windows-mcp/SKILL.md`，内容是给 Agent 的操作规则：

- 只在用户明确提出桌面相关请求时使用
- 先截屏确认画面，再决定下一步，一次只做一个动作
- 点击、输入、关闭窗口、删除文件等会改变系统状态的操作，先向用户确认
- 遇到密码、支付等敏感界面时停止操作并告知用户

Skill 只是对模型的行为约束，不会增加权限，也不会替代 MCP 工具本身。

## 安全提示

- 插件**不做调用者权限判断**。凡是能触发 Agent 的人，理论上都可能让它操作你的电脑。建议只给自己专用的自定义人格启用这些工具和技能，不要放进群聊使用的默认人格。
- Skill 里的规则只是对模型的约束，不是真正的权限控制。
- 停止服务时，插件会结束监听所配置端口的进程（包括不是本插件启动的）。请确认该端口没有被其他重要程序占用，必要时在配置里换一个端口。

## 日志与排查

服务的所有输出都会进入 AstrBot 日志，在日志中搜索 `[windows-mcp]` 即可查看。

| 现象 | 可能原因与处理 |
| --- | --- |
| `No interpreter found for Python 3.14` | uv 在插件环境里找不到该版本。检查 `uv_python_dir` 是否填成了**目录**，且里面确实有对应版本的托管 Python |
| `Failed to download ...` / `operation timed out` | 下载超时。调大 `http_timeout`，或检查镜像源和网络（代理、防火墙） |
| 请求被发到了和 `index_url` 不同的源 | 可能有 `pyproject.toml`、`uv.toml` 或环境变量指定了其他源。插件已设置 `UV_NO_CONFIG` 并清理了相关环境变量，仍出现请检查 `package_spec` 和网络 |
| 提示端口仍未就绪，但进程还在 | 通常是首次构建较慢，查看 `[windows-mcp]` 日志的进度，等待即可 |
| 日志显示进程提前退出 | 查看退出码前后的 `[windows-mcp]` 日志，里面有 uv 或服务的具体报错 |
| 浏览器访问 `/mcp` 提示 400 | 正常现象。直接用 GET 访问 MCP 端点缺少必要请求头，不代表服务异常 |
| MCP 页面显示未连接 | 确认服务已启动（端口在监听），再点击刷新按钮 |
| Agent 仍然调用不存在的工具 | 多为旧会话上下文或记忆里残留了旧工具名。用 `/new` 开新会话，并清理相关记忆 |

## 目录结构

```
astrbot_plugin_win_proxy/
├── metadata.yaml
├── main.py
├── _conf_schema.json
├── README.md
└── skills/
    └── windows-mcp/
        └── SKILL.md
```

## 致谢

本插件托管并使用 [CursorTouch/Windows-MCP](https://github.com/CursorTouch/Windows-MCP) 提供的服务，使用时请遵守该项目的开源许可协议。
