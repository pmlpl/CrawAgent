"""全局配置 — 使用 Pydantic Settings 管理环境变量

字段名直接对应 .env 中的变量名（大小写不敏感）。
"""
import os
import socket
from pathlib import Path
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def clear_dead_proxy_env() -> None:
    """清除指向不可达代理的环境变量。

    终端/IDE 里可能残留代理工具写入的 HTTP_PROXY/HTTPS_PROXY/ALL_PROXY，
    若代理软件已关闭，httpx/requests 仍会尝试走该代理 → WinError 10061 拒绝连接，
    拖垮 LLM、LangSmith、爬虫等所有 HTTP 请求。这里对每个已设置的代理做一次 TCP 探测，
    不可达则为本进程清除（不影响系统配置），可达则保留。
    """
    proxy_keys = ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]
    for key in proxy_keys:
        val = os.environ.get(key)
        if not val:
            continue
        try:
            parsed = urlparse(val if "://" in val else "http://" + val)
            host = parsed.hostname
            if not host:
                continue
            port = parsed.port or (443 if "https" in val.lower() else 80)
            with socket.create_connection((host, port), timeout=1.5):
                pass  # 代理可达，保留
        except OSError:
            os.environ.pop(key, None)


class Settings(BaseSettings):
    """项目配置，从 .env 文件读取"""

    model_config = SettingsConfigDict(
        # 绝对路径：不依赖进程启动时的工作目录（CWD），
        # 否则从非项目根目录启动服务会读不到 .env，导致 API Key 显示为空
        env_file=_PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 模型层（变量名与 .env 一致） ----
    openai_api_key: str = ""
    openai_base_url: str = "https://api.deepseek.com/v1"
    default_model: str = "deepseek-v4-flash"
    # 可选的多模型列表，逗号分隔；为空时前端会回退到 default_model
    models: str = ""
    # 思考深度：off / low / medium / high / max（max→API 传 xhigh；off 不传 reasoning_effort）
    # 映射为 reasoning_effort 参数传给 LLM（DeepSeek 官方合法值：none / low / medium / high / xhigh）
    # off 时不传 reasoning_effort，模型按默认行为执行
    thinking_depth: str = "off"
    # 多服务商模型注册表（WebUI 设置页维护）：JSON 数组，每项
    # {"name": 服务商名, "base_url": 接口地址, "api_key": 密钥, "models": [模型 ID...]}
    # 为空时回退到上面的旧全局 openai_* 配置
    llm_providers: str = ""
    # 视频子 Agent 专用模型：与主 Agent 分开 → DeepSeek 缓存按"账号+模型"隔离，
    # 子代理大量调用不会把主 agent 的历史前缀从服务端 LRU 缓存中挤掉。
    # 红线：不要与主 Agent 当前模型相同（同模型 = 同缓存池，命中率会被挤到 50%）。
    # 子代理未来若需识图，改成视觉模型即可（只要仍与主 Agent 模型不同就保持隔离）。
    video_finder_model: str = "deepseek-v4-flash"

    # ---- 爬虫工具层 ----
    request_timeout: int = 30
    request_delay: float = 1.0
    max_content_length: int = 50000
    ffmpeg_path: str = ""
    bilibili_cookie: str = ""
    # VIP 章节代理 API（番茄小说等）；留空则不启用代理回退
    locked_chapter_api: str = ""
    # 站点级登录 Cookie 不再放全局配置：随站点档案存 data/sites.json，
    # 由站点工具（如 weread_tool）按 origin 读取。

    # ---- 产物目录（统一配置，grep 不用全项目搜字符串 "downloads"/"output"）----
    # downloads_dir：媒体下载（图片/视频/音频/壁纸/封面）的根目录。工具/脚本应写入
    #                <downloads_dir>/<子目录>/，避免大文件混到文本产物里。
    # 注：用模块级 _PROJECT_ROOT 而非 project_root，是因为 Pydantic v2 class body
    #    里字段声明顺序靠后的 field 不能被前面的默认值引用（NameError）。
    #    db_path/sessions_db_path/log_dir 下一行同样写法。
    downloads_dir: Path = _PROJECT_ROOT / "downloads"
    # output_dir：文本产物（md/txt/json/结构化报告）的根目录。save_to_file 默认写入
    #             <output_dir>/<子目录>/。
    output_dir: Path = _PROJECT_ROOT / "output"

    # ---- Skills 扩展（目录内每个子目录含 SKILL.md 即为一个 skill）----
    # 分号分隔的多个目录；相对路径基于项目根。索引在 Agent 构建时静态注入
    # system prompt（不破坏 DeepSeek 前缀缓存），正文由 read_skill 工具按需读取。
    skills_dirs: str = "skills"
    # ------------------------------------------------------------------

    # ---- MCP 服务器（Model Context Protocol）----
    # JSON 数组，每项为 langchain-mcp-adapters 的连接配置，例：
    # [{"name":"anything","transport":"sse","url":"http://127.0.0.1:23816/sse"}]
    # [{"name":"fs","transport":"stdio","command":"npx","args":["-y","@modelcontextprotocol/server-filesystem","D:/data"]}]
    # 留空 = 不启用 MCP。构建 Agent 时把这些 server 的工具并入工具列表。
    mcp_servers: str = ""
    # True = MCP server 未运行时自动在后台拉起 MCP_START_COMMAND（前端开关）
    MCP_AUTOSTART: bool = False
    # 拉起 MCP 服务的命令（shell 执行，如 bootstrap 脚本或 docker start ...）
    MCP_START_COMMAND: str = ""
    # ------------------------------------------------------------------

    # ---- 存储层 ----
    project_root: Path = _PROJECT_ROOT
    db_path: Path = project_root / "data" / "crawagent.db"
    # 会话记忆持久化（LangGraph SqliteSaver），按 session_id(thread_id) 存取对话状态
    sessions_db_path: Path = project_root / "data" / "sessions.db"
    log_dir: Path = project_root / "logs"

    # ---- 资源治理（内存 LRU 上限 + 磁盘保留策略，防只增不减）----
    max_cached_agents: int = 8        # 不同模型的 compiled agent 缓存上限
    max_tracked_sessions: int = 512   # _metrics + _session_locks 的跟踪上限
    # 磁盘保留策略：None = 该维度不清理；天数按 mtime，容量超限从最老文件砍
    logs_retention_days: int | None = 30
    output_retention_days: int | None = 90
    downloads_retention_days: int | None = 60
    output_max_size_gb: float | None = None
    downloads_max_size_gb: float | None = 5.0

    # ---- LangSmith 调试（可选） ----
    langsmith_api_key: str = ""
    langsmith_project: str = "crawagent"

    # ---- 历史消息裁剪（Token 膨胀治理） ----
    # 传给 LLM 的历史 messages token 上限（不含 system prompt）
    # None = 动态：按当前模型 context_window × 70% 自动算（推荐）
    # 显式值（如 32000）= 强制固定水位，忽略模型上下文差异
    history_max_tokens: int | None = None
    # 滑动窗口保留的最近完整轮数（1 轮 = Human + AI + 中间 Tool）
    history_keep_recent_turns: int = 3
    # 单条 ToolMessage 内容裁剪阈值（超过则保留首尾）
    # 增大到 2000，保留更多上下文给 LLM，同时截断仍然是确定性的（不破坏缓存前缀）
    tool_result_max_chars: int = 2000
    langsmith_tracing: bool = False


def get_settings() -> Settings:
    """获取全局配置单例

    额外将 LangSmith 配置同步到 os.environ——
    LangChain/LangGraph 运行时会直接读取环境变量（LANGSMITH_*）来决定是否开启链路追踪，
    仅靠 pydantic-settings 读取 .env 不会自动注入进程环境。
    """
    settings = Settings()
    if settings.langsmith_api_key:
        os.environ.setdefault("LANGSMITH_API_KEY", settings.langsmith_api_key)
        os.environ.setdefault("LANGSMITH_TRACING", "true" if settings.langsmith_tracing else "false")
        os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)
    return settings
