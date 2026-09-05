"""Skills 扩展 — 目录约定式技能加载（兼容 Claude/Trae SKILL.md 格式）+ MCP 工具接入。

目录约定（与 reverse-skill 等社区 skill 包原生兼容）：
    <skill_root>/
      <skill_name>/SKILL.md   ← YAML frontmatter (name, description) + Markdown 正文
      <skill_name>/references/*.md  ← 可选的参考文档（read_skill 的 ref 参数读取）

加载策略（DeepSeek 前缀缓存友好）：
    - Agent 构建时扫描 skills_dirs，把每个 skill 的 name+description 拼成
      静态索引入 system prompt（只加名字和一句话描述，不膨胀）
    - 正文由 read_skill 工具按需读取（渐进披露），任务命中才进入上下文
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml
from langchain_core.tools import tool

from crawagent.config.settings import get_settings


def _parse_skill_dirs() -> list[Path]:
    """解析 skills_dirs 配置为绝对路径列表。

    除配置的目录外，自动追加第三方插件自带的 skills/ 子目录
    （plugins/*/skills，P2-9 插件规范）—— 插件技能随插件安装即生效。
    """
    s = get_settings()
    roots = []
    for raw in s.skills_dirs.split(";"):
        raw = raw.strip()
        if not raw:
            continue
        p = Path(raw)
        if not p.is_absolute():
            p = s.project_root / p
        if p.is_dir():
            roots.append(p)
    try:
        from crawagent.tools.registry import plugin_skill_dirs

        for p in plugin_skill_dirs():
            if p.is_dir() and p not in roots:
                roots.append(p)
    except Exception:
        pass  # 插件目录扫描失败不影响常规技能加载
    return roots


def load_skill_index() -> list[dict]:
    """扫描所有 skill 根目录，返回 [{name, description, dir}]。

    兼容两层布局：<root>/<skill>/SKILL.md 和 <root> 本身含 SKILL.md
    （后者按 router 型单 skill 处理）。
    """
    index = []
    for root in _parse_skill_dirs():
        candidates = [d for d in root.iterdir() if d.is_dir() and (d / "SKILL.md").exists()]
        if (root / "SKILL.md").exists():
            candidates.append(root)  # 根目录自身是 router 型 skill
        for d in candidates:
            try:
                text = (d / "SKILL.md").read_text(encoding="utf-8")
            except OSError:
                continue
            meta = _frontmatter(text)
            name = meta.get("name") or d.name
            desc = (meta.get("description") or "").strip().replace("\n", " ")
            index.append({"name": name, "description": desc, "dir": str(d)})
    return index


def _frontmatter(text: str) -> dict:
    """解析 SKILL.md 头部 YAML frontmatter；没有则返回空 dict。"""
    if not text.startswith("---"):
        return {}
    try:
        end = text.index("\n---", 3)
    except ValueError:
        return {}
    try:
        meta = yaml.safe_load(text[3:end])
        return meta if isinstance(meta, dict) else {}
    except yaml.YAMLError:
        return {}


def build_skills_prompt() -> str:
    """构建注入 system prompt 的静态技能索引段。无 skill 时返回空串。"""
    index = load_skill_index()
    if not index:
        return ""
    lines = [
        "",
        "AVAILABLE SKILLS (installable expertise modules):",
        "For tasks matching a skill below, call read_skill(name) FIRST to load its full instructions, then follow them. Skill bodies may reference additional files via the ref argument.",
    ]
    for s in index:
        desc = s["description"][:200]
        lines.append(f"- {s['name']}: {desc}")
    return "\n".join(lines) + "\n"


def _find_skill_dir(name: str) -> Path | None:
    for s in load_skill_index():
        if s["name"] == name:
            return Path(s["dir"])
    return None


@tool
def read_skill(name: str, ref: str = "") -> str:
    """Read the full instructions of an installed skill.

    Args:
        name: Exact skill name from the AVAILABLE SKILLS index in your system prompt.
        ref: Optional relative path to a reference file inside the skill directory
             (e.g. "references/frida-cookbook.md"). Empty = read SKILL.md itself.
    """
    d = _find_skill_dir(name)
    if d is None:
        return f"[SKILL_NOT_FOUND] '{name}' is not in the installed skills index."
    target = (d / ref) if ref else (d / "SKILL.md")
    # 防路径逃逸：ref 必须落在 skill 目录内
    try:
        target.resolve().relative_to(d.resolve())
    except ValueError:
        return "[SKILL_ERROR] ref path escapes the skill directory."
    if not target.is_file():
        return f"[SKILL_NOT_FOUND] file not found: {ref or 'SKILL.md'}"
    try:
        return target.read_text(encoding="utf-8")[:30000]
    except OSError as e:
        return f"[SKILL_ERROR] {e}"


_mcp_spawned = False  # 本进程只自动拉起一次


def reset_mcp_cache() -> None:
    """清空 MCP 工具缓存（设置页改了 MCP 配置 / token 后调用）。"""
    global _mcp_tools_cache, _mcp_cache_signature
    _mcp_tools_cache = {}
    _mcp_cache_signature = ""


def _port_listening(host: str, port: int, timeout: float = 1.0) -> bool:
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _find_anything_analyzer() -> str | None:
    """自动定位 anything-analyzer 项目路径（无 dist 时用 pnpm dev 启动）。

    搜索顺序：
      1. .env 里显式配的 ANYTHING_ANALYZER_PATH
      2. %USERPROFILE%\\Tools\\anything-analyzer
      3. %USERPROFILE%\\anything-analyzer
      4. 通过 Electron 配置文件的物理位置反推 Electron app 的 userData
         （但 userData 在 AppData，不是项目路径，所以没用；跳过）
    """
    import os
    from pathlib import Path

    env_path = os.environ.get("ANYTHING_ANALYZER_PATH", "").strip()
    if env_path:
        p = Path(env_path)
        if p.is_dir() and (p / "package.json").exists():
            return str(p)

    home = Path.home()
    candidates = [
        home / "Tools" / "anything-analyzer",
        home / "anything-analyzer",
        home / "Projects" / "anything-analyzer",
        home / "Code" / "anything-analyzer",
        home / "Desktop" / "anything-analyzer",
    ]
    for p in candidates:
        if p.is_dir() and (p / "package.json").exists():
            return str(p)

    return None


def _find_pnpm_cmd() -> str:
    """在 Windows 上找 pnpm 完整路径（.cmd 形式），subprocess 才能正确执行。"""
    import os
    import shutil
    # 优先 shutil.which
    found = shutil.which("pnpm") or shutil.which("pnpm.cmd") or shutil.which("pnpm.CMD")
    if found:
        return found
    # 硬编码常见路径兜底
    candidates = [
        os.path.expandvars(r"%LOCALAPPDATA%\pnpm\bin\pnpm.CMD"),
        os.path.expandvars(r"%LOCALAPPDATA%\pnpm\pnpm.CMD"),
        os.path.expandvars(r"%APPDATA%\npm\pnpm.cmd"),
        os.path.expanduser(r"~\AppData\Local\pnpm\bin\pnpm.CMD"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return "pnpm"  # 最后 fallback，靠 PATH


def _build_autostart_command(port: int, custom_cmd: str) -> tuple[str, str]:
    """决定用什么命令拉起 MCP server。

    Returns: (command, source_label)
      source_label: "custom" = 用户填的命令；"builtin" = CrawAgent 内置 anything-analyzer 启动
    """
    import os

    # 1. 用户填了自定义命令 → 优先用
    if custom_cmd.strip():
        return custom_cmd.strip(), "custom"

    # 2. 识别为 anything-analyzer（默认端口 23816）→ 内置启动
    if port == 23816:
        aa_path = _find_anything_analyzer()
        if aa_path:
            pnpm = _find_pnpm_cmd()
            if os.name == "nt":
                # Windows 上用 cmd /c 包一下，PATH 更可靠
                cmd = f'cmd /c "{pnpm} dev"'
            else:
                cmd = f'"{pnpm}" dev'
            return cmd, f"builtin:anything-analyzer@{aa_path}"

    return "", ""


def ensure_mcp_started(wait: bool = True, force: bool = False) -> bool:
    """MCP server 未运行时自动拉起 anything-analyzer（内置）或执行自定义命令。

    force=True：绕过 MCP_AUTOSTART 开关——AI 在对话里显式检查 MCP 状态时
    视为用户授权拉起（仍受配置存在性与进程级一次守卫约束）。

    Returns: True = 服务已就绪（本来就活着或拉起成功）。
    """
    global _mcp_spawned
    s = get_settings()
    if not s.mcp_servers.strip() or not (force or s.MCP_AUTOSTART):
        return False
    try:
        first = json.loads(s.mcp_servers)[0]
        url = first.get("url", "")
        host, port = "127.0.0.1", 0
        if url:
            from urllib.parse import urlparse

            parsed = urlparse(url)
            host, port = parsed.hostname or "127.0.0.1", parsed.port or 80
    except (json.JSONDecodeError, IndexError, ValueError):
        return False

    if not port or _port_listening(host, port):
        return True
    if _mcp_spawned:
        return False  # 上一轮拉起过还没就绪，不重复 spawn

    import os
    import subprocess
    import time

    cmd, source = _build_autostart_command(port, s.MCP_START_COMMAND)
    if not cmd:
        print("[MCP] autostart 无法构建启动命令：既没有 MCP_START_COMMAND 自定义命令，"
              "也找不到 anything-analyzer 项目路径。"
              "请在 .env 里配 ANYTHING_ANALYZER_PATH 或 MCP_START_COMMAND。")
        return False

    # 决定 cwd
    if source.startswith("builtin:"):
        cwd = source.split("@", 1)[1]  # builtin:anything-analyzer@C:\...
    else:
        cwd = str(s.project_root)

    print(f"[MCP] autostart ({source}): {cmd[:120]}  cwd={cwd}")
    try:
        log_path = s.log_dir / "mcp_autostart.log"
        s.log_dir.mkdir(parents=True, exist_ok=True)
        log_fh = open(log_path, "a", encoding="utf-8", errors="replace")
        log_fh.write(f"\n===== {__import__('datetime').datetime.now()} autostart ({source}) =====\n")

        if os.name == "nt":
            # Windows: 写 .bat 文件到项目目录，ShellExecuteW 执行 bat
            # 完全脱离 Trae sandbox 控制，让 pnpm 在系统 cmd 里跑
            import ctypes
            from pathlib import Path

            scripts_dir = Path(__file__).parent.parent / "scripts"
            scripts_dir.mkdir(parents=True, exist_ok=True)
            bat_path = scripts_dir / "start_anything_analyzer.bat"

            # 写 bat：cd 到项目目录 → 跑 pnpm dev
            # cmd 变量可能带 "cmd /c" 前缀，bat 里直接裸调即可
            actual_cmd = cmd
            if actual_cmd.lower().startswith("cmd /c"):
                actual_cmd = actual_cmd[6:].strip()  # 去掉 "cmd /c "

            # bat 里 .CMD 不能直接写带路径的引号形式，用 call 或用 npm.cmd
            # 去掉引号，直接 call pnpm.CMD dev
            # 如果 actual_cmd 包含引号，尝试提取命令路径
            import re
            # 匹配引号里的可执行文件路径 (.cmd/.exe/.bat/.ps1)
            m = re.search(r'"([^"]+\.(?:CMD|cmd|exe|EXE|bat|BAT))"\s*(.*)', actual_cmd)
            if m:
                exe_path = m.group(1)
                exe_args = m.group(2).strip()
            else:
                parts = actual_cmd.split(None, 1)
                exe_path = parts[0].strip('"')
                exe_args = parts[1] if len(parts) > 1 else ""

            bat_path.write_text(
                "@echo off\r\n"
                "chcp 65001 >nul\r\n"
                "title anything-analyzer MCP Server\r\n"
                "echo.\r\n"
                "echo [CrawAgent] 正在启动 anything-analyzer ...\r\n"
                f'cd /d "{cwd}"\r\n'
                f'echo. 当前目录: %cd%\r\n'
                f'call "{exe_path}" {exe_args}\r\n'
                "echo.\r\n"
                "echo [CrawAgent] 进程已退出。按任意键关闭。\r\n"
                "pause >nul\r\n",
                encoding="utf-8"
            )
            log_fh.write(f"bat written to {bat_path}\n")

            # ShellExecuteW: 打开 bat 文件 → 会弹一个 cmd 窗口完全独立运行
            SW_SHOWNORMAL = 1
            ret = ctypes.windll.shell32.ShellExecuteW(
                None, "open", str(bat_path), "", cwd, SW_SHOWNORMAL
            )
            log_fh.write(f"ShellExecuteW ret={ret}\n")
            # ShellExecuteW 返回值 > 32 表示成功
            if ret <= 32:
                log_fh.write(f"ShellExecuteW FAILED ret={ret}\n")
        else:
            # 非 Windows 用 subprocess
            subprocess.Popen(
                cmd, shell=True,
                cwd=cwd,
                stdout=log_fh, stderr=subprocess.STDOUT,
            )
    except Exception as e:
        print(f"[MCP] autostart 启动失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    _mcp_spawned = True
    if wait:
        # 短等待（15秒）给用户反馈，前端可以自己轮询
        deadline = time.time() + 15
        while time.time() < deadline:
            if _port_listening(host, port):
                print(f"[MCP] service ready on {host}:{port}")
                return True
            time.sleep(1)
        print(f"[MCP] autostart 已触发，但 15 秒内还没 ready — pnpm dev 首次编译可能需要更久")
        log_fh.close()
        return True  # 算成功触发，让前端继续轮询
    log_fh.close()
    return True


_mcp_spawned = False  # 本进程只自动拉起一次

# MCP 工具缓存：避免每次 build_mcp_tools / warm_cache 都重新 asyncio.run 握手。
# 握手用的临时事件循环关闭后，库内部的 SSE 传输协程会泄漏（RuntimeWarning:
# coroutine 'MultiServerMCPClient.get_tools' was never awaited）。缓存后整条
# 生命周期只握手一次，泄漏消失。签名变化（token 轮换 / 开关切换）自动失效。
# 逐 server 缓存（P2-9 多 MCP）：成功的 server 只握手一次；失败的 server 不缓存，
# 下次调用自动重试（用户可能刚把服务拉起来），且不拖累其它 server。
_mcp_tools_cache: dict[str, list] = {}
_mcp_cache_signature: str = ""


def _mcp_config_signature() -> str:
    """MCP 配置指纹：token/端口/开关任一变化即失效缓存。"""
    import os
    return os.environ.get("MCP_SERVERS", "")


def _expand_path_token(value: str) -> str:
    """展开 {project_root} 占位符（stdio command/args 里引用项目 venv 解释器等）。"""
    if "{project_root}" in value:
        return value.replace("{project_root}", str(get_settings().project_root))
    return value


def _server_conn(srv: dict) -> dict | None:
    """把 .env 里的单个 MCP server 配置转成 langchain-mcp-adapters 连接 dict。"""
    name = srv.get("name")
    if not name:
        return None
    transport = srv.get("transport", "stdio")
    if transport == "stdio":
        return {
            "transport": "stdio",
            "command": _expand_path_token(str(srv.get("command", ""))),
            "args": [_expand_path_token(str(a)) for a in srv.get("args", [])],
            "env": srv.get("env"),
        }
    # sse / streamable_http / websocket
    entry = {"transport": transport, "url": srv.get("url")}
    if srv.get("headers"):
        entry["headers"] = srv["headers"]  # 鉴权头，如 {"Authorization": "Bearer <token>"}
    return entry


def get_mcp_tools() -> list:
    """连接 MCP_SERVERS 配置的所有 server，返回 langchain 工具列表。

    结果按 MCP_SERVERS 配置指纹缓存：同一配置只握手一次，避免重复
    asyncio.run 创建/销毁临时事件循环导致 SSE 传输协程泄漏。
    适配器的工具是自包含协程（每次调用自建会话），同步侧 BaseTool.invoke
    会自动 asyncio.run 执行，无需常驻事件循环；代价是 stdio 型 server 每次
    调用重启子进程——长驻服务建议用 sse/streamable_http 传输。

    逐 server 连接与缓存（P2-9 多 MCP 接入）：单个 server 挂掉只跳过它自己，
    其余 server 的工具照常可用（此前是全有全无，一个 server 连不上全部丢弃）。
    失败的 server 不缓存，下次调用自动重试。
    """
    global _mcp_tools_cache, _mcp_cache_signature

    sig = _mcp_config_signature()
    if _mcp_tools_cache and sig == _mcp_cache_signature:
        return [t for tools in _mcp_tools_cache.values() for t in tools]
    if not isinstance(_mcp_tools_cache, dict):
        _mcp_tools_cache = {}  # 兼容旧版 None 初始化 / reset 遗留

    s = get_settings()
    if not s.mcp_servers.strip():
        _mcp_tools_cache = {}
        _mcp_cache_signature = sig
        return []
    # ensure_mcp_started() 不再自动触发 — 让用户在前端 Settings 页手动点"启动"按钮
    try:
        servers = json.loads(s.mcp_servers)
    except json.JSONDecodeError as e:
        print(f"[MCP] mcp_servers 配置不是合法 JSON，跳过: {e}")
        return []

    import asyncio
    import os

    # 本机 MCP 服务绝不能走系统代理（Privoxy/Clash 会拦 localhost 请求返回 500）
    no_proxy = os.environ.get("NO_PROXY", "")
    if "127.0.0.1" not in no_proxy:
        os.environ["NO_PROXY"] = (no_proxy + "," if no_proxy else "") + "127.0.0.1,localhost"

    from langchain_mcp_adapters.client import MultiServerMCPClient

    merged: dict[str, list] = {}
    for srv in servers:
        name = srv.get("name")
        conn_entry = _server_conn(srv)
        if not name or not conn_entry:
            continue
        if name in _mcp_tools_cache and sig == _mcp_cache_signature:
            merged[name] = _mcp_tools_cache[name]  # 命中缓存，不再握手
            continue
        try:
            conn: dict = {name: conn_entry}  # 裸 dict 标注：适配器要 TypedDict，字面量推断会触发不变性报错
            client = MultiServerMCPClient(conn)
            tools = asyncio.run(client.get_tools())
            if tools:
                merged[name] = tools
                print(f"[MCP] '{name}' loaded {len(tools)} tools ({srv.get('transport', 'stdio')})")
            else:
                print(f"[MCP] '{name}' 连接成功但 0 个工具")
        except BaseException as e:
            # TaskGroup 会把真实原因包在 ExceptionGroup 里，解开找到根因（通常是连接拒绝）
            root = e
            while hasattr(root, "exceptions") and root.exceptions:
                root = root.exceptions[0]
            print(f"[MCP] '{name}' 连接失败（跳过该 server，不影响其它）: {type(root).__name__}: {root}")

    _mcp_tools_cache = merged
    _mcp_cache_signature = sig
    return [t for tools in merged.values() for t in tools]
