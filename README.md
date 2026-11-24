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

插件负责 Windows-MCP 服务的生命周期管理，并提供少量管理指令。它不会在普通对话中主动回复。

## 功能

- 插件加载时按配置自动启动 Windows-MCP，在后台等待就绪，不阻塞 AstrBot 启动
- 插件卸载或重载时自动清理子进程
- 通过 `uvx` 运行 Windows-MCP，使用 Python `3.14`
- 自动复用 AstrBot 全局 PyPI 镜像源，无需在插件中重复配置
- 使用 uv 默认的 HTTP 超时和重试策略
- 服务输出统一转发到 AstrBot 日志，前缀为 `[windows-mcp]`
- 支持安全、标准、完全三档工具权限配置
- 支持额外排除指定工具
- 支持限制插件管理指令的使用者
- 随插件提供 `windows-mcp` Skill，在 WebUI 的 `插件 → 技能` 中显示

## 环境要求

- Windows（停止服务时会调用 PowerShell 清理端口占用）
- AstrBot `>=4.16`
- 已安装 [uv](https://docs.astral.sh/uv/)，并确保 `uvx` 可以通过系统 PATH 找到，或在插件配置中指定 `uvx.exe` 路径
- 能访问 AstrBot 全局配置中指定的 PyPI 镜像源，或能访问 PyPI 官方源

## 安装

1. 将本插件目录放入 AstrBot 的 `data/plugins/` 下，或在 WebUI 的插件页面安装。
2. 根据需要修改插件配置（见下表）。
3. 重载插件。若开启了 `auto_start`，会自动开始启动服务。

**首次启动需要下载并构建依赖，可能要几分钟，期间请不要重载插件**，否则子进程会被清理，需要重新启动构建流程。依赖进入 uv 缓存后，之后的启动会快很多。

## 配置项

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `auto_start` | `true` | 加载插件时自动启动 Windows-MCP |
| `host` | `127.0.0.1` | Windows-MCP 监听地址 |
| `port` | `8000` | Windows-MCP 监听端口 |
| `tool_tier` | `safe` | 默认工具权限档位，可选 `safe`、`standard`、`full` |
| `extra_exclude_tools` | `[]` | 额外排除的 Windows-MCP 工具名称列表 |
| `admin_ids` | `[]` | 允许使用插件管理指令的用户 ID 列表；留空时不限制 |
| `uvx_path` | 空 | `uvx.exe` 的完整路径；留空时自动从系统 PATH 查找 |

### 工具权限档位

插件通过 Windows-MCP 的 `--exclude-tools` 参数控制工具可用范围。

| 档位 | 默认排除的工具 | 说明 |
| --- | --- | --- |
| `safe` | 高危工具和桌面交互工具 | 默认档位，限制 PowerShell、文件系统、注册表、进程及桌面操作工具 |
| `standard` | 高危工具 | 保留桌面交互能力，但排除 PowerShell、文件系统、注册表和进程工具 |
| `full` | 无 | 不额外排除工具，开放 Windows-MCP 提供的全部工具 |

`extra_exclude_tools` 可以在默认档位的基础上继续排除工具。它不会恢复当前档位已经排除的工具。

### 管理指令

| 指令 | 作用 |
| --- | --- |
| `/stop_winmcp` | 停止 Windows-MCP 服务 |
| `/set_tier safe` | 切换到安全档位并重启服务 |
| `/set_tier standard` | 切换到标准档位并重启服务 |
| `/set_tier full` | 切换到完全档位并重启服务 |
| `/set_tier` | 查询当前档位及使用方法 |

如果配置了 `admin_ids`，只有列表中的用户可以执行这些管理指令。留空时不限制指令使用者。

### PyPI 镜像源

插件不再单独提供 PyPI 镜像源配置，而是读取 AstrBot 的全局配置：

`配置文件 → 高级 → PyPI 镜像源`

修改 AstrBot 的全局镜像源后，插件启动时会自动使用对应地址。如果全局配置为空，则使用 uv 默认的软件包索引源。

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

- `admin_ids` 只限制 `/stop_winmcp`、`/set_tier` 等插件管理指令的使用者，**不限制 Agent 对 MCP 工具的调用权限**。
- 请谨慎选择启用了 Windows-MCP 工具和 Skill 的人格，不要轻易将其放入群聊使用的默认人格。
- Skill 里的规则只是对模型的行为约束，不是真正的权限控制。
- `full` 档位会开放 Windows-MCP 提供的全部工具，请谨慎使用。
- 停止服务时，插件会尝试结束监听所配置端口的进程（包括不是本插件启动的进程）。请确认该端口没有被其他重要程序占用，必要时在配置里换一个端口。
- 建议仅在可信任的本机环境中运行，不要将 MCP 服务端口暴露到公网。

## 日志与排查

服务的所有输出都会进入 AstrBot 日志，在日志中搜索 `[windows-mcp]` 即可查看。

| 现象 | 可能原因与处理 |
| --- | --- |
| 找不到 `uvx` 或启动时提示命令不存在 | 检查是否已安装 uv，确认 `uvx.exe` 已加入系统 PATH，或在插件配置中填写正确的 `uvx_path` |
| `No interpreter found for Python 3.14` | 检查 uv 是否能够下载或找到 Python `3.14`，并确认网络连接正常 |
| `Failed to download ...` / `operation timed out` | 检查 AstrBot 全局 PyPI 镜像源和网络连接（代理、防火墙等）；uv 使用自身默认的超时和重试策略 |
| 请求被发到了和 AstrBot 全局镜像配置不同的源 | 检查 AstrBot 的 `pypi_index_url` 配置，以及插件启动日志中的命令和环境变量 |
| 提示端口仍未就绪，但进程还在 | 通常是首次构建较慢，查看 `[windows-mcp]` 日志的进度，等待即可 |
| 日志显示进程提前退出 | 查看退出码前后的 `[windows-mcp]` 日志，里面有 uv 或服务的具体报错 |
| 浏览器访问 `/mcp` 提示 400 | 正常现象。直接用 GET 访问 MCP 端点缺少必要请求头，不代表服务异常 |
| MCP 页面显示未连接 | 确认服务已启动（端口在监听），再点击刷新按钮 |
| Agent 仍然调用不存在的工具 | 多为旧会话上下文或记忆里残留了旧工具名。用 `/new` 开新会话，并清理相关记忆 |

## 目录结构

```text
astrbot_plugin_win_proxy/
├── metadata.yaml
├── main.py
├── _conf_schema.json
├── README.md
└── skills/
    └── SKILL.md
```

## 致谢

本插件托管并使用 [CursorTouch/Windows-MCP](https://github.com/CursorTouch/Windows-MCP) 提供的服务，使用时请遵守该项目的开源许可协议。