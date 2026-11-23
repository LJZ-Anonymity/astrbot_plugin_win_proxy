from typing import Optional
import contextlib
import subprocess
import threading
import asyncio
import socket
import os

from astrbot.api.star import Context, Star, register
from astrbot.api import logger


@register(
    "astrbot_plugin_win_proxy",
    "local",
    "管理本地 Windows-MCP 服务的自动启动与停止",
    "0.3.0",
)
class WinProxyPlugin(Star):
    """Windows-MCP 生命周期管理插件（桌面操作由 MCP + Skill 提供）。"""

    # 首次构建依赖可能较慢，给足等待时间（秒）
    START_TIMEOUT_SEC = 180

    def __init__(self, context: Context, config: Optional[dict] = None):
        super().__init__(context)
        self.config: dict = config or {}
        self.proc: Optional[subprocess.Popen] = None
        self._start_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    async def initialize(self):
        """插件初始化；若开启 auto_start，则在后台启动，不阻塞插件加载。"""
        host, port = self._endpoint()
        auto_start = bool(self.config.get("auto_start", True))
        logger.info(
            f"astrbot_plugin_win_proxy 初始化，host={host}, port={port}, auto_start={auto_start}"
        )
        if auto_start:
            self._start_task = asyncio.create_task(self._auto_start())

    async def _auto_start(self):
        try:
            host, port = self._endpoint()
            # 缓解重载竞态：端口可能刚好处于旧进程尚未释放的状态
            if await self._is_port_open(host, port):
                await asyncio.sleep(2.5)
            started, msg = await self._start_server(self.START_TIMEOUT_SEC)
            logger.info(f"auto_start 结果: started={started}, msg={msg}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(f"auto_start 异常: {exc}")

    async def terminate(self):
        """插件卸载或重载时调用，清理托管的 Windows-MCP 进程。"""
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
        """探测目标端口是否已监听。"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.5)
                return sock.connect_ex((host, port)) == 0
        except OSError as exc:
            logger.warning(f"探测端口 {host}:{port} 失败: {exc}")
            return False

    async def _is_port_open(self, host: str, port: int) -> bool:
        return await asyncio.to_thread(self._check_port_open, host, port)

    def _build_clean_env(self) -> dict[str, str]:
        """复制当前环境变量，清理干扰项，并显式配置 uv。"""
        env = os.environ.copy()
        for key in (
            "VIRTUAL_ENV",
            "PYTHONHOME",
            "PYTHONPATH",
            "UV_NO_MANAGED_PYTHON",
            "UV_CONFIG_FILE",
            "UV_INDEX_URL",
            "UV_EXTRA_INDEX_URL",
        ):
            env.pop(key, None)

        env["UV_PYTHON_PREFERENCE"] = "managed"
        env["UV_NO_CONFIG"] = "1"

        index_url = str(self.config.get("index_url", "")).strip()
        if index_url:
            env["UV_DEFAULT_INDEX"] = index_url

        uv_python_dir = str(self.config.get("uv_python_dir", "")).strip()
        if uv_python_dir:
            env["UV_PYTHON_INSTALL_DIR"] = uv_python_dir
            env["UV_PYTHON_DOWNLOADS"] = "never"

        # 大包（numpy 等）在慢速镜像下容易超时
        try:
            http_timeout = int(self.config.get("http_timeout", 300))
        except (TypeError, ValueError):
            http_timeout = 300
        try:
            http_retries = int(self.config.get("http_retries", 5))
        except (TypeError, ValueError):
            http_retries = 5
        env["UV_HTTP_TIMEOUT"] = str(max(30, http_timeout))
        env["UV_HTTP_RETRIES"] = str(max(1, http_retries))

        return env

    def _pump_output(self, proc: subprocess.Popen):
        """在线程中持续读取子进程输出并转发到 AstrBot 日志。

        必须一直读取，否则管道缓冲区写满后子进程会被阻塞。
        """
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
        """兜底清理占用指定端口的进程（Windows）。"""
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

    # ------------------------------------------------------------------
    # 启动 / 停止
    # ------------------------------------------------------------------
    async def _start_server(self, timeout_sec: int = 180) -> tuple[bool, str]:
        """启动本地 Windows-MCP HTTP 服务，并轮询等待端口就绪。"""
        host, port = self._endpoint()

        if await self._is_port_open(host, port):
            return False, f"Windows-MCP 已经在运行中（{host}:{port}）。"

        uvx_path = str(
            self.config.get(
                "uvx_path",
                r"E:\QQBot\.astrbot_launcher\components\uv\uvx.exe",
            )
        )
        py_ver = str(self.config.get("python_version", "3.14"))
        index_url = str(
            self.config.get("index_url", "https://mirrors.aliyun.com/pypi/simple/")
        )
        package_spec = str(
            self.config.get("package_spec", "windows-mcp==0.9.0")
        ).strip() or "windows-mcp"

        cmd = [
            uvx_path,
            "--python",
            py_ver,
            "--index-url",
            index_url,
            "--from",
            package_spec,
            "windows-mcp",
            "serve",
            "--transport",
            "streamable-http",
            "--host",
            host,
            "--port",
            str(port),
        ]

        env = self._build_clean_env()

        logger.info(f"准备启动 Windows-MCP，命令: {cmd}")
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
                return True, f"Windows-MCP 已启动并监听 {host}:{port}。"
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
        """停止由本插件启动或占用目标端口的 Windows-MCP 服务，并等待端口释放。"""
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

        # 等待端口真正释放，避免紧接着的重载竞态
        for _ in range(10):
            if not await self._is_port_open(host, port):
                break
            await asyncio.sleep(0.5)

        if stopped:
            return True, "已关闭 Windows-MCP。"
        return False, "Windows-MCP 当前未在运行。"