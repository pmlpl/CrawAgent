"""
Agent 视频播放工具
供 Agent 调用，自动启动播放器服务器并打开浏览器播放视频

使用方法:
    from crawagent.tools.video_agent import watch_video
    watch_video("奔跑吧兄弟")  # 自动启动服务器并打开浏览器播放
"""

import webbrowser
import urllib.parse
import threading
import time
import os
import sys
import subprocess
import socket

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from crawagent.config.settings import get_logger

logger = get_logger(__name__)


_server_process = None  # 全局保存服务器进程
_startup_error = None   # 保存启动错误信息


def _is_port_open(port: int) -> bool:
    """检查端口是否已被占用（服务器是否已启动）"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            result = s.connect_ex(('127.0.0.1', port))
            return result == 0
    except:
        return False


def _check_flask_available() -> tuple[bool, str]:
    """检查当前 Python 环境是否有 Flask
    
    Returns:
        (bool, str): (是否可用, 详细信息)
    """
    try:
        import flask
        return True, f"Flask {flask.__version__}"
    except ImportError as e:
        return False, str(e)


def _start_server(port: int = 5000) -> tuple[bool, str]:
    """启动视频播放器 Flask 服务器
    
    Args:
        port: 端口号
        
    Returns:
        (bool, str): (是否成功启动, 状态信息/错误信息)
    """
    global _server_process, _startup_error
    
    # 检查是否已启动
    if _is_port_open(port):
        return True, "服务器已在运行"
    
    # 先检查 Flask 是否可用
    flask_ok, flask_info = _check_flask_available()
    if not flask_ok:
        err = f"当前 Python 环境缺少 Flask: {flask_info}\n请运行: {sys.executable} -m pip install flask"
        _startup_error = err
        logger.error(f"[视频播放器] {err}")
        return False, err
    
    # 找到 video_player_app.py 的路径
    # 优先用项目根目录下的
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    app_path = os.path.join(project_root, 'video_player_app.py')
    
    if not os.path.exists(app_path):
        # 如果找不到，尝试从当前目录找
        app_path = 'video_player_app.py'
    
    if not os.path.exists(app_path):
        err = f"找不到 video_player_app.py (项目根目录: {project_root})"
        _startup_error = err
        return False, err
    
    # 启动 Flask 应用（后台运行）
    try:
        env = os.environ.copy()
        env['FLASK_ENV'] = 'development'
        # 确保子进程用同样的 Python
        python_exe = sys.executable
        
        # Windows 下用 python 运行，创建新的终端窗口（用户可见）
        if sys.platform == 'win32':
            _server_process = subprocess.Popen(
                [python_exe, app_path],
                cwd=project_root,
                env=env,
                creationflags=subprocess.CREATE_NEW_CONSOLE if hasattr(subprocess, 'CREATE_NEW_CONSOLE') else 0,
            )
        else:
            _server_process = subprocess.Popen(
                [python_exe, app_path],
                cwd=project_root,
                env=env,
            )
        
        logger.info(f"[视频播放器] 启动服务器: python={python_exe}, app={app_path}")
        logger.info(f"[视频播放器] 进程 PID: {_server_process.pid}")
        
        # 等待服务器启动（最多等 15 秒）
        for i in range(30):
            time.sleep(0.5)
            # 检查进程是否退出了
            if _server_process.poll() is not None:
                # 进程已退出，读取错误信息
                try:
                    stderr = _server_process.stderr.read().decode('utf-8', errors='ignore')
                except:
                    stderr = "无法读取错误输出"
                err = f"服务器进程异常退出 (code={_server_process.returncode})\n错误输出: {stderr[:800]}"
                _startup_error = err
                print(f"[视频播放器] 启动失败: {err}")
                return False, err
            
            if _is_port_open(port):
                # 再多等 1 秒让 Flask 完全就绪
                time.sleep(1)
                logger.info(f"[视频播放器] 服务器启动成功，端口 {port}")
                return True, "启动成功"
        
        # 超时了，再检查一次
        if _is_port_open(port):
            return True, "启动成功"
        
        # 还是没起来，尝试读取 stderr
        try:
            stderr = _server_process.stderr.read().decode('utf-8', errors='ignore')
        except:
            stderr = ""
        err = f"服务器启动超时（15秒内端口未开放）\n错误输出: {stderr[:500]}"
        _startup_error = err
        return False, err
        
    except Exception as e:
        err = f"启动服务器异常: {e}"
        _startup_error = err
        logger.error(f"[视频播放器] {err}")
        return False, err


def watch_video(name: str, port: int = 5000) -> str:
    """播放视频 - 自动启动服务器并打开浏览器
    
    Args:
        name: 视频名称（支持电影、电视剧、综艺等）
        port: 播放器服务端口，默认 5000
    
    Returns:
        str: 播放结果说明 + URL
    """
    # 1. 检查并启动服务器
    server_ready, server_msg = _start_server(port)
    
    # 2. 构造播放 URL
    encoded_name = urllib.parse.quote(name)
    player_url = f"http://localhost:{port}/?name={encoded_name}"
    
    # 3. 只有服务器启动成功才打开浏览器
    if server_ready:
        def open_browser():
            time.sleep(0.5)
            webbrowser.open(player_url)
        
        thread = threading.Thread(target=open_browser)
        thread.start()
        
        return f"🎬 正在为您播放: {name}\n\n播放器已启动，浏览器将自动打开。\n\n如果浏览器没有自动打开，请手动访问: {player_url}"
    else:
        # 服务器启动失败，给出错误信息和手动启动方式
        return (
            f"❌ 播放器服务器启动失败\n\n"
            f"错误信息:\n{server_msg}\n\n"
            f"请尝试手动启动:\n"
            f"  1. 打开新的终端\n"
            f"  2. 进入项目目录: cd {os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))}\n"
            f"  3. 运行: {sys.executable} video_player_app.py\n"
            f"  4. 然后在浏览器打开: {player_url}"
        )


def get_player_url(name: str, port: int = 5000) -> str:
    """获取播放页面 URL（不打开浏览器）
    
    Args:
        name: 视频名称
        port: 播放器服务端口，默认 5000
    
    Returns:
        str: 播放页面 URL
    """
    encoded_name = urllib.parse.quote(name)
    return f"http://localhost:{port}/?name={encoded_name}"


__all__ = ['watch_video', 'get_player_url']

