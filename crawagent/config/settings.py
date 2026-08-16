"""全局配置 — 使用 Pydantic Settings 管理环境变量

字段名直接对应 .env 中的变量名（大小写不敏感）。
"""
import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


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

    # ---- 爬虫工具层 ----
    request_timeout: int = 30
    request_delay: float = 1.0
    max_content_length: int = 50000
    ffmpeg_path: str = ""
    bilibili_cookie: str = ""
    # VIP 章节代理 API（番茄小说等）；留空则不启用代理回退
    locked_chapter_api: str = ""

    # ---- 存储层 ----
    project_root: Path = _PROJECT_ROOT
    db_path: Path = project_root / "data" / "crawagent.db"
    # 会话记忆持久化（LangGraph SqliteSaver），按 session_id(thread_id) 存取对话状态
    sessions_db_path: Path = project_root / "data" / "sessions.db"
    log_dir: Path = project_root / "logs"

    # ---- LangSmith 调试（可选） ----
    langsmith_api_key: str = ""
    langsmith_project: str = "crawagent"

    # ---- 历史消息裁剪（Token 膨胀治理） ----
    # 传给 LLM 的历史 messages token 上限（不含 system prompt）
    history_max_tokens: int = 8000
    # 滑动窗口保留的最近完整轮数（1 轮 = Human + AI + 中间 Tool）
    history_keep_recent_turns: int = 3
    # 单条 ToolMessage 内容裁剪阈值（超过则保留首尾）
    tool_result_max_chars: int = 500
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
