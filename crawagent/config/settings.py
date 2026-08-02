from __future__ import annotations

from functools import lru_cache
from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 应用基础
    app_name: str = "CrawAgent"
    app_version: str = "1.0.0"
    debug: bool = True
    host: str = "0.0.0.0"
    port: int = 8000

    # MySQL 配置
    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_user: str = "crawagent"
    mysql_password: str = "crawagent"
    mysql_database: str = "crawagent"
    mysql_pool_size: int = 10
    mysql_echo: bool = False

    # Redis 配置
    redis_url: str = "redis://localhost:6379/0"

    # SQLite 路径（兼容旧功能）
    data_dir: str = "data"
    frontier_db_path: str = "data/frontier.db"
    retriever_db_path: str = "data/retriever.db"
    checkpointer_db_path: str = "data/checkpoints.db"

    # LLM 配置（任意 OpenAI 兼容 API）
    openai_api_key: Optional[str] = None
    openai_base_url: str = "https://api.openai.com/v1"
    default_model: str = "gpt-4o-mini"
    default_temperature: float = 0.1
    max_tokens: int = 4096
    llm_request_timeout: float = 60.0
    mock_mode: bool = False

    # Ollama 本地（可选）
    ollama_base_url: Optional[str] = None

    # 爬虫配置
    per_domain_rate: float = 0.5  # req/sec per domain
    max_concurrent: int = 50
    request_timeout: float = 30.0
    max_retries: int = 3
    max_pages: int = 100
    max_depth: int = 3

    # 反爬配置
    impersonate: str = "chrome120"
    use_curl_cffi: bool = True
    proxy_pool: List[str] = []
    custom_user_agents: List[str] = []

    # Agent Harness 配置
    harness_max_turns: int = 50
    harness_compaction_threshold: int = 80000  # token 数，超过触发压缩
    harness_default_lane: str = "main"

    # 日志
    log_level: str = "INFO"
    log_file: str = "logs/crawagent.log"

    @property
    def mysql_dsn(self) -> str:
        """MySQL 连接字符串（async）"""
        return (
            f"mysql+aiomysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"
        )


@lru_cache()
def get_settings() -> Settings:
    """获取配置单例"""
    return Settings()
