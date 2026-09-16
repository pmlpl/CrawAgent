"""嵌入 Redis 自动管理 — 检测/下载/启动 redis-server 子进程。

设计意图：
    - `uv run crawagent start` 时自动调 ensure_redis()
    - 先尝试连已有 Redis → 连得上直接用
    - 连不上 → 找系统 redis-server → 有就 subprocess.Popen 起子进程
    - 系统没装 → Windows 从 GitHub tporadowski/redis releases 下载 portable Redis
      到 data/redis/（类似 Playwright 自动下载 Chromium）
    - 下载失败 → 提示用户手动装

用户什么都不用装，首次自动下载约 5MB，后续直接用本地。
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import zipfile
from pathlib import Path

import requests

from crawagent.config.settings import get_settings

# 嵌入 Redis 子进程句柄
_redis_process: subprocess.Popen | None = None

# Windows portable Redis 下载地址（tporadowski/redis releases）
_WIN_REDIS_URL = "https://github.com/tporadowski/redis/releases/download/v5.0.14.1/Redis-x64-5.0.14.1.zip"
_WIN_REDIS_VERSION = "5.0.14.1"


def _try_connect(host: str = "127.0.0.1", port: int = 6379, timeout: float = 1.0) -> bool:
    """尝试 TCP 连接 Redis 端口，判断是否已在运行。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _find_redis_binary() -> str | None:
    """查找系统 redis-server 二进制（shutil.which + data/redis/ 下找）。"""
    # 1. shutil.which 找系统 PATH
    binary = shutil.which("redis-server")
    if binary:
        return binary
    # 2. data/redis/ 下找已下载的 portable 版
    settings = get_settings()
    redis_dir = settings.project_root / "data" / "redis"
    if sys.platform == "win32":
        exe = redis_dir / "redis-server.exe"
    else:
        exe = redis_dir / "redis-server"
    if exe.exists():
        return str(exe)
    return None


def _download_portable_redis() -> str | None:
    """下载 Windows portable Redis 到 data/redis/。

    返回 redis-server.exe 路径，失败返回 None。
    """
    settings = get_settings()
    redis_dir = settings.project_root / "data" / "redis"
    redis_dir.mkdir(parents=True, exist_ok=True)

    if sys.platform != "win32":
        # 非 Windows：尝试用包管理器提示
        print("[redis] 非 Windows 系统，请手动安装 redis-server")
        return None

    zip_path = redis_dir / "redis.zip"
    exe_path = redis_dir / "redis-server.exe"

    # 已下载过就直接用
    if exe_path.exists():
        return str(exe_path)

    print(f"[redis] 下载 portable Redis ({_WIN_REDIS_VERSION}) 到 {redis_dir}...")
    try:
        resp = requests.get(_WIN_REDIS_URL, timeout=60, stream=True)
        resp.raise_for_status()
        with open(zip_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        print(f"[redis] 下载完成 ({zip_path.stat().st_size // 1024}KB)，解压中...")
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(redis_dir)
        zip_path.unlink(missing_ok=True)
        if exe_path.exists():
            print(f"[redis] 解压完成: {exe_path}")
            return str(exe_path)
        # 有时 exe 在子目录
        for child in redis_dir.rglob("redis-server.exe"):
            return str(child)
        print("[redis] 解压后未找到 redis-server.exe")
        return None
    except Exception as e:
        print(f"[redis] 下载失败: {type(e).__name__}: {e}")
        print("[redis] 请手动安装 Redis 或设置 REDIS_URL 环境变量指向远程 Redis")
        return None


def _start_redis_subprocess(binary_path: str, port: int = 6379) -> bool:
    """用 subprocess.Popen 启动 redis-server 子进程。"""
    global _redis_process
    args = [binary_path, "--port", str(port), "--save", "", "--appendonly", "no"]
    try:
        # Windows: CREATE_NO_WINDOW 避免弹黑窗
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        _redis_process = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **kwargs,
        )
        # 等待启动（最多 3 秒）
        import time
        for _ in range(30):
            if _try_connect(port=port):
                return True
            time.sleep(0.1)
        return False
    except Exception as e:
        print(f"[redis] 启动 redis-server 子进程失败: {e}")
        _redis_process = None
        return False


def ensure_redis() -> bool:
    """确保 Redis 可用 — 自动检测/下载/启动。

    调用链：连已有 → 找系统二进制 → 下载 portable → 起子进程。
    返回 True = Redis 已就绪；False = 无法启动（提示用户手动装）。
    """
    settings = get_settings()

    # 如果用户配了 REDIS_URL 指向远程 Redis，直接用
    if settings.redis_url:
        if _try_connect():
            print(f"[redis] connected to existing redis at {settings.redis_url}")
            return True
        # 远程 Redis 连不上，不走嵌入启动
        print(f"[redis] WARNING: REDIS_URL={settings.redis_url} 但连接失败")
        return False

    # 1. 先试连本地默认端口
    if _try_connect():
        print("[redis] connected to existing redis at 127.0.0.1:6379")
        return True

    # 2. 找系统 redis-server
    binary = _find_redis_binary()

    # 3. 没找到 → 下载
    if not binary:
        print("[redis] 系统未安装 redis-server，尝试自动下载...")
        binary = _download_portable_redis()

    if not binary:
        print("[redis] 无法自动获取 redis-server")
        print("[redis] 请手动安装 Redis：")
        if sys.platform == "win32":
            print("  Windows: https://github.com/tporadowski/redis/releases")
        else:
            print("  Linux: sudo apt install redis-server")
            print("  macOS: brew install redis")
        print("  或设置环境变量 REDIS_URL=redis://your-redis-host:6379/0")
        return False

    # 4. 起子进程
    print(f"[redis] starting embedded redis-server ({binary})...")
    if _start_redis_subprocess(binary):
        print("[redis] embedded redis started at 127.0.0.1:6379")
        return True

    print("[redis] 启动嵌入 redis-server 失败")
    return False


def stop_embedded_redis() -> None:
    """关闭由 ensure_redis() 启动的 redis-server 子进程。"""
    global _redis_process
    if _redis_process is not None:
        try:
            _redis_process.terminate()
            _redis_process.wait(timeout=5)
        except Exception:
            try:
                _redis_process.kill()
            except Exception:
                pass
        _redis_process = None
        print("[redis] embedded redis stopped")
