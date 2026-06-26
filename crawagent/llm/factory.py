"""LLM 工厂 —— 统一接口

支持两种 provider:
  - "openai"     —— OpenAI / DeepSeek / LM Studio / Ollama / 通义千问 等一切兼容 API
  - "anthropic"  —— Anthropic Claude 系列

统一暴露为 LangChain BaseChatModel 接口。
"""
from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from ..config.settings import ModelConfig, Settings


# ============================================================
# Provider 创建函数
# ============================================================

def _create_openai(cfg: ModelConfig) -> BaseChatModel:
    """OpenAI 兼容 API（OpenAI / DeepSeek / LM Studio / 通义千问 等）"""
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as e:
        raise RuntimeError(
            "缺少依赖 langchain-openai。请运行: pip install langchain-openai"
        ) from e

    # 本地服务（LM Studio 等）不需要 API key，但 ChatOpenAI 要求非空字符串
    api_key = cfg.api_key if cfg.api_key else "not-needed"

    return ChatOpenAI(
        model=cfg.model_name,
        api_key=api_key,
        base_url=cfg.base_url,
        temperature=cfg.temperature,
        max_tokens=cfg.max_tokens,
    )


def _create_anthropic(cfg: ModelConfig) -> BaseChatModel:
    """Anthropic Claude 系列"""
    try:
        from langchain_anthropic import ChatAnthropic
    except ImportError as e:
        raise RuntimeError(
            "缺少依赖 langchain-anthropic。请运行: pip install langchain-anthropic"
        ) from e

    if not cfg.api_key:
        raise RuntimeError(
            f"Anthropic 模型 '{cfg.name}' 需要配置 api_key。"
            "请在 models.json 中填写 Anthropic API Key。"
        )

    return ChatAnthropic(
        model=cfg.model_name,
        anthropic_api_key=cfg.api_key,
        temperature=cfg.temperature,
        max_tokens=cfg.max_tokens,
    )


_PROVIDERS = {
    "openai": _create_openai,
    "anthropic": _create_anthropic,
}


# ============================================================
# LLM 工厂
# ============================================================

class LLMFactory:

    def __init__(self, settings: Settings):
        self._settings = settings
        self._cache: dict[str, BaseChatModel] = {}
        self._current: str = settings.current

    def get_llm(self, name: str) -> BaseChatModel:
        cfg = self._settings.get_model(name)
        if not cfg:
            available = ", ".join(m.name for m in self._settings.list_models())
            raise ValueError(f"未找到模型 '{name}'。可用: {available}")
        if name in self._cache:
            return self._cache[name]

        creator = _PROVIDERS.get(cfg.provider)
        if not creator:
            raise ValueError(
                f"未知 provider: '{cfg.provider}'。"
                "只支持 openai / anthropic"
            )

        llm = creator(cfg)
        self._cache[name] = llm
        return llm

    def get_default(self) -> BaseChatModel:
        if not self._current:
            raise ValueError("未配置任何模型，请先 /add_model 添加")
        return self.get_llm(self._current)

    def current_name(self) -> str:
        return self._current

    def switch_to(self, name: str) -> tuple[bool, str]:
        cfg = self._settings.get_model(name)
        if not cfg:
            return False, f"未找到模型 '{name}'"
        try:
            self.get_llm(name)
        except Exception as e:
            return False, f"模型加载失败: {e}"
        self._current = name
        self._settings.set_current(name)
        return True, f"已切换到: {cfg.display()}"

    def list_models(self) -> list[tuple[str, str, str]]:
        """返回 [(name, display_string, provider), ...]"""
        return [(m.name, m.display(), m.provider) for m in self._settings.list_models()]

    # ---------- 直接对话 ----------

    def chat(
        self,
        user_msg: str,
        system_msg: str = "你是 CrawAgent，一个专注于网页爬取与数据整理的助手。回答简洁明了。",
        model_name: str | None = None,
    ) -> str:
        try:
            llm = self.get_llm(model_name) if model_name else self.get_default()
        except Exception as e:
            return f"[LLM 不可用] {e}\n请用 /add_model 添加一个模型"

        messages: list[BaseMessage] = [
            SystemMessage(content=system_msg),
            HumanMessage(content=user_msg),
        ]
        try:
            resp: AIMessage = llm.invoke(messages)
            return str(resp.content)
        except Exception as e:
            return f"[LLM 调用失败] {e}"


def to_chat_messages(
    user_msg: str,
    system_msg: str | None = None,
    history: list[tuple[str, str]] | None = None,
) -> list[BaseMessage]:
    msgs: list[BaseMessage] = []
    if system_msg:
        msgs.append(SystemMessage(content=system_msg))
    if history:
        for role, text in history:
            if role == "user":
                msgs.append(HumanMessage(content=text))
            elif role == "ai":
                msgs.append(AIMessage(content=text))
    msgs.append(HumanMessage(content=user_msg))
    return msgs
