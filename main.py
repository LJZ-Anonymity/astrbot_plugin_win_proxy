from typing import Optional
import contextlib
import subprocess
import threading
import asyncio
import socket
import shutil
import os

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.core import astrbot_config
from astrbot.api import logger

# 三档默认排除的高危/越权工具集合（按需结合实际 windows-mcp 版本工具名微调）
HIGH_RISK_TOOLS = [
    "PowerShell",
    "FileSystem",
    "Registry",
    "Process",
]

UI_CONTROL_TOOLS = [
    "App",
    "Click",
    "Type",
    "Scroll",
    "Move",
    "Shortcut",
    "MultiSelect",
    "MultiEdit",
    "Clipboard",
    "Notification",
]

TIER_EXCLUDE_MAP: dict[str, list[str]] = {
    "safe": HIGH_RISK_TOOLS + UI_CONTROL_TOOLS,
    "standard": HIGH_RISK_TOOLS,
    "full": [],
}


@register(
    "astrbot_plugin_win_proxy",
    "local",
    "管理本地 Windows-MCP 服务的自动启动、停止与工具权限档位",
    "0.2.0",
)
class WinProxyPlugin(Star):
    """Windows-MCP 生命周期与工具分级管理插件。"""

    START_TIMEOUT_SEC = 180

    def __init__(self, context: Context, config: Optional[dict] = None):
        super().__init__(context)
        self.config: dict = config or {}
        self.proc: Optional[subprocess.Popen] = None
        self._start_task: Optional[asyncio.Task] = None
        self._current_tier: str = self._normalize_tier(self.config.get("tool_tier"))

    # ------------------------------------------------------------------
    # 档位与权限辅助
    # ------------------------------------------------------------------
    def _normalize_tier(self, raw_tier: Optional[str]) -> str:
        tier = str(raw_tier or "safe").strip().lower()
        if tier in ("safe", "安全"):
            return "safe"
        if tier in ("standard", "标准"):
            return "standard"
        if tier in ("full", "完全"):
            return "full"
        return "safe"

    def _build_exclude_tools(self, tier: str) -> str:
        excluded: list[str] = list(TIER_EXCLUDE_MAP.get(tier, TIER_EXCLUDE_MAP["safe"]))
        extra_raw = self.config.get("extra_exclude_tools") or []

        if isinstance(extra_raw, list):
            candidates = [str(item) for item in extra_raw]
        else:
            candidates = str(extra_raw).split(",")

        for item in candidates:
            name = item.strip()
            if name and name not in excluded:
                excluded.append(name)
        return ",".join(excluded)

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        admin_ids = self.config.get("admin_ids") or []
        if not admin_ids:
            return True
        allowed = {str(item).strip() for item in admin_ids if str(item).strip()}
        if not allowed:
            return True
        sender_id = str(event.get_sender_id() or "").strip()
        return sender_id in allowed

    # ------------------------------------------------------------------
    # 指令处理
    # ------------------------------------------------------------------
    @filter.command("stop_winmcp")
    async def stop_winmcp_cmd(self, event: AstrMessageEvent):
        """直接结束 Windows-MCP 进程。"""
        if not self._is_admin(event):
            yield event.plain_result("无权执行此操作。")
            return

        if self._start_task is not None and not self._start_task.done():
            self._start_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._start_task
        self._start_task = None

        stopped, msg = await self._stop_server()
        logger.info(
            f"指令 stop_winmcp，sender={event.get_sender_id()}，stopped={stopped}，msg={msg}"
        )
        yield event.plain_result(msg)

    @filter.command("set_tier")
    async def set_tier_cmd(self, event: AstrMessageEvent, tier_arg: str = ""):
        """切换工具档位（safe / standard / full）并重启 Windows-MCP 服务。"""
        if not self._is_admin(event):
            yield event.plain_result("无权执行此操作。")
            return

        target_tier = self._normalize_tier(tier_arg)
        if not tier_arg.strip():
            yield event.plain_result(
                f"当前档位：{self._current_tier}。用法：/set_tier safe | standard | full"
            )
            return

        self.config["tool_tier"] = target_tier
        self._current_tier = target_tier
        yield event.plain_result(f"正在切换到 [{target_tier}] 档位并重启 Windows-MCP...")

        ok, msg = await self._restart_server()
        yield event.plain_result(f"档位已设为 [{target_tier}]。{msg}" if ok else f"切换失败：{msg}")

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    async def initialize(self):
        host, port = self._endpoint()
        auto_start = bool(self.config.get("auto_start", True))
        self._current_tier = self._normalize_tier(self.config.get("tool_tier"))
        logger.info(
            f"astrbot_plugin_win_proxy 初始化，host={host}, port={port}, "
            f"tier={self._current_tier}, auto_start={auto_start}"
        )
        if auto_start:
            self._start_task = asyncio.create_task(self._auto_start())

    async def _auto_start(self):
        try:
            host, port = self._endpoint()
            if await self._is_port_open(host, port):
                await asyncio.sleep(2.5)
            started, msg = await self._start_server(self.START_TIMEOUT_SEC)
            logger.info(f"auto_start 结果: started={started}, msg={msg}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(f"auto_start 异常: {exc}")

    async def terminate(self):
        logger.info("astrbot_plugin_win_proxy 正在卸载/重载，清理 Windows-MCP 进程...")
        if self._start_task is not None and not self._start_task.done():
            self._start_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._start_task
        self._start_task = None
        await self._stop_server()

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------
    def _endpoint(self) -> tuple[str, int]:
        host = str(self.config.get("host", "127.0.0.1"))
        port = int(self.config.get("port", 8000))
        return host, port

    def _check_port_open(self, host: str, port: int) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.5)
                return sock.connect_ex((host, port)) == 0
        except OSError as exc:
            logger.warning(f"探测端口 {host}:{port} 失败: {exc}")
            return False

    async def _is_port_open(self, host: str, port: int) -> bool:
        return await asyncio.to_thread(self._check_port_open, host, port)

    def _get_pypi_index_url(self) -> str:
        """读取 AstrBot 全局 PyPI 镜像源配置。"""
        try:
            index_url = str(
                astrbot_config.get("pypi_index_url", "") or ""
            ).strip()

            if index_url:
                return index_url

        except Exception as exc:
            logger.warning(f"读取 AstrBot PyPI 镜像源失败: {exc}")

        # 全局配置读取失败或为空时，使用 uv 默认源
        return ""

    def _build_clean_env(self) -> dict[str, str]:
        env = os.environ.copy()
        for key in (
            "VIRTUAL_ENV",
            "PYTHONHOME",
            "PYTHONPATH",
            "UV_NO_MANAGED_PYTHON",
            "UV_CONFIG_FILE",
            "UV_INDEX_URL",
            "UV_EXTRA_INDEX_URL",
            "UV_DEFAULT_INDEX",  # 新增
        ):
            env.pop(key, None)

        env["UV_PYTHON_PREFERENCE"] = "managed"
        env["UV_NO_CONFIG"] = "1"

        index_url = self._get_pypi_index_url()
        if index_url:
            env["UV_DEFAULT_INDEX"] = index_url

        return env

    def _pump_output(self, proc: subprocess.Popen):
        stream = proc.stdout
        if stream is None:
            return
        try:
            for raw in stream:
                line = raw.rstrip()
                if not line:
                    continue
                if line.lower().startswith("error"):
                    logger.error(f"[windows-mcp] {line}")
                else:
                    logger.info(f"[windows-mcp] {line}")
        except Exception as exc:
            logger.warning(f"[windows-mcp] 读取输出中断: {exc}")
        finally:
            with contextlib.suppress(Exception):
                stream.close()

    def _kill_port_owner_sync(self, port: int) -> bool:
        ps_cmd = (
            f"Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue "
            f"| ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force }}"
        )
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            return True
        except Exception as exc:
            logger.error(f"清理占用端口 {port} 的进程失败: {exc}")
            return False

    def _resolve_uvx_path(self) -> str:
        """优先使用配置路径；留空时从环境变量或系统 PATH 自动查找 uvx。"""
        configured = str(self.config.get("uvx_path") or "").strip()
        if configured:
            return configured

        for candidate in ("uvx.exe", "uvx"):
            found = shutil.which(candidate)
            if found:
                return found

        return "uvx"

    # ------------------------------------------------------------------
    # 启动 / 停止 / 重启
    # ------------------------------------------------------------------
    async def _start_server(self, timeout_sec: int = 180) -> tuple[bool, str]:
        host, port = self._endpoint()
        tier = self._normalize_tier(self.config.get("tool_tier"))
        self._current_tier = tier

        if await self._is_port_open(host, port):
            return False, f"Windows-MCP 已经在运行中（{host}:{port}）。"

        uvx_path = self._resolve_uvx_path()
        MIN_PYTHON_VERSION = "3.14"
        DEFAULT_PACKAGE_SPEC = "windows-mcp==0.9.0"  # 锁MCP版本

        cmd = [
            uvx_path,
            "--python",
            MIN_PYTHON_VERSION,
        ]

        # 复用 AstrBot 全局 PyPI 镜像源
        index_url = self._get_pypi_index_url()
        if index_url:
            cmd.extend(["--index-url", index_url])

        cmd.extend([
            "--from",
            DEFAULT_PACKAGE_SPEC,
            "windows-mcp",
            "serve",
            "--transport",
            "streamable-http",
            "--host",
            host,
            "--port",
            str(port),
        ])

        exclude_tools = self._build_exclude_tools(tier)
        if exclude_tools:
            cmd.extend(["--exclude-tools", exclude_tools])

        env = self._build_clean_env()

        logger.info(f"准备启动 Windows-MCP（档位={tier}），命令: {cmd}")
        try:
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
                subprocess, "CREATE_NO_WINDOW", 0
            )
            proc = await asyncio.to_thread(
                subprocess.Popen,
                cmd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
        except Exception as exc:
            logger.error(f"启动 Windows-MCP 失败: {exc}")
            self.proc = None
            return False, f"启动 Windows-MCP 失败：{exc}"

        self.proc = proc
        threading.Thread(
            target=self._pump_output,
            args=(proc,),
            name="winmcp-log",
            daemon=True,
        ).start()

        for _ in range(max(1, int(timeout_sec))):
            await asyncio.sleep(1)
            if await self._is_port_open(host, port):
                return True, f"Windows-MCP 已按 [{tier}] 档位启动并监听 {host}:{port}。"
            if proc.poll() is not None:
                code = proc.returncode
                self.proc = None
                return False, (
                    f"Windows-MCP 进程提前退出（退出码 {code}），"
                    "请在 AstrBot 日志中搜索 [windows-mcp] 查看原因。"
                )

        return False, (
            f"等待 {timeout_sec} 秒后端口 {host}:{port} 仍未就绪，进程仍在运行，"
            "可能仍在首次构建环境，请在 AstrBot 日志中搜索 [windows-mcp] 查看进度。"
        )

    async def _stop_server(self) -> tuple[bool, str]:
        host, port = self._endpoint()
        stopped = False

        proc = self.proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                await asyncio.to_thread(proc.wait, 5)
            except subprocess.TimeoutExpired:
                proc.kill()
                await asyncio.to_thread(proc.wait, 3)
            except Exception as exc:
                logger.error(f"终止 Windows-MCP 子进程异常: {exc}")
            finally:
                stopped = True
        self.proc = None

        if await self._is_port_open(host, port):
            await asyncio.to_thread(self._kill_port_owner_sync, port)
            stopped = True

        for _ in range(10):
            if not await self._is_port_open(host, port):
                break
            await asyncio.sleep(0.5)

        if stopped:
            return True, "已关闭 Windows-MCP。"
        return False, "Windows-MCP 当前未在运行。"

    async def _restart_server(self) -> tuple[bool, str]:
        await self._stop_server()
        return await self._start_server(self.START_TIMEOUT_SEC)