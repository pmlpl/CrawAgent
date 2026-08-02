from __future__ import annotations

from functools import lru_cache
import os
from typing import Any, Dict, List, Optional

# 禁用系统代理，防止 Privoxy 等代理拦截 API 请求
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from crawagent.config.settings import get_settings


class LLMProvider(BaseModel):
    """LLM 提供商配置"""
    name: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: str = ""
    temperature: float = 0.1
    max_tokens: int = 4096
    extra_params: Dict[str, Any] = Field(default_factory=dict)


class LLMFactory:
    """多提供商 LLM 工厂"""

    def __init__(self):
        self._providers: Dict[str, LLMProvider] = {}
        self._clients: Dict[str, BaseChatModel] = {}
        self._request_timeout: float = 300.0
        self._load_from_settings()

    def _load_from_settings(self) -> None:
        """从配置加载提供商"""
        settings = get_settings()
        self._request_timeout = settings.llm_request_timeout
        
        # Mock 模式优先
        if settings.mock_mode:
            self._providers["mock"] = LLMProvider(
                name="mock",
                model="mock-model",
                temperature=0.0,
                max_tokens=4096,
            )
            return
        
        # OpenAI 兼容接口（DeepSeek/OpenAI/智谱/百川/月之暗面/...）
        if settings.openai_api_key:
            self._providers["openai"] = LLMProvider(
                name="openai",
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url,
                model=settings.default_model,
                temperature=settings.default_temperature,
                max_tokens=settings.max_tokens,
            )

        # Ollama 本地
        if settings.ollama_base_url:
            self._providers["ollama"] = LLMProvider(
                name="ollama",
                base_url=settings.ollama_base_url,
                model=settings.default_model,
                temperature=settings.default_temperature,
                max_tokens=settings.max_tokens,
            )

    def register_provider(self, provider: LLMProvider) -> None:
        """注册新提供商"""
        self._providers[provider.name] = provider
        self._clients.pop(provider.name, None)

    def get_provider(self, name: str) -> Optional[LLMProvider]:
        return self._providers.get(name)

    def list_providers(self) -> List[str]:
        return list(self._providers.keys())

    def _create_client(self, provider: LLMProvider) -> BaseChatModel:
        """创建 LLM 客户端"""
        if provider.name == "mock":
            from crawagent.llm.mock import MockLLM
            return MockLLM(
                model=provider.model,
                temperature=provider.temperature,
            )
        
        if provider.name == "ollama":
            from langchain_community.chat_models import ChatOllama
            return ChatOllama(
                model=provider.model,
                base_url=provider.base_url,
                temperature=provider.temperature,
                **provider.extra_params,
            )
        
        # 任意 OpenAI 兼容 API
        from langchain_openai import ChatOpenAI

        base_url = provider.base_url or ""
        is_local = any(host in base_url for host in ["localhost", "127.0.0.1", "10.", "172.", "192.168."])
        
        if is_local:
            os.environ["no_proxy"] = base_url.split("://")[1].split("/")[0] if "://" in base_url else base_url
            os.environ["NO_PROXY"] = os.environ["no_proxy"]
            os.environ["LANGCHAIN_OPENAI_TCP_KEEPALIVE"] = "0"

        client_kwargs = dict(
            model=provider.model,
            api_key=provider.api_key,
            base_url=base_url,
            temperature=provider.temperature,
            max_tokens=provider.max_tokens,
            timeout=self._request_timeout,
        )
        return ChatOpenAI(**provider.extra_params, **client_kwargs)

    def get_llm(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        **kwargs
    ) -> BaseChatModel:
        """获取 LLM 实例（带缓存，使用 _clients 字典替代 @lru_cache）"""
        if provider:
            provider_name = provider
        elif "mock" in self._providers:
            provider_name = "mock"
        elif "openai" in self._providers:
            provider_name = "openai"
        else:
            provider_name = list(self._providers.keys())[0] if self._providers else "openai"
        
        if provider_name not in self._providers:
            raise ValueError(f"Provider not found: {provider_name}. Available: {list(self._providers.keys())}")

        provider_config = self._providers[provider_name]
        
        final_model = model or provider_config.model
        final_temp = temperature if temperature is not None else provider_config.temperature

        cache_key = f"{provider_name}:{final_model}:{final_temp}"

        # 只缓存基础 client，不缓存绑定工具的版本（避免不同工具集串扰）
        if cache_key in self._clients:
            base_client = self._clients[cache_key]
        else:
            provider_copy = provider_config.model_copy(update={
                "model": final_model,
                "temperature": final_temp,
            })
            base_client = self._create_client(provider_copy)
            self._clients[cache_key] = base_client

        # 绑定工具时返回新的 RunnableBinding，不污染缓存
        if kwargs.get("tools"):
            return base_client.bind_tools(kwargs["tools"])

        return base_client

    def get_llm_with_tools(
        self,
        tools: List[BaseTool],
        provider: Optional[str] = None,
        **kwargs
    ) -> BaseChatModel:
        """获取绑定工具的 LLM"""
        return self.get_llm(provider=provider, tools=tools, **kwargs)

    def clear_cache(self) -> None:
        self._clients.clear()

    def reload(self) -> None:
        """重新加载配置（修改设置后调用）"""
        self._providers.clear()
        self._clients.clear()
        self._load_from_settings()


_factory = None

@lru_cache()
def get_factory() -> LLMFactory:
    global _factory
    if _factory is None:
        _factory = LLMFactory()
    return _factory


def get_llm(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    **kwargs
) -> BaseChatModel:
    """获取 LLM 实例"""
    factory = get_factory()
    return factory.get_llm(provider=provider, model=model, temperature=temperature, **kwargs)


def get_llm_with_tools(
    tools: List[BaseTool],
    provider: Optional[str] = None,
    **kwargs
) -> BaseChatModel:
    """获取绑定工具的 LLM"""
    factory = get_factory()
    return factory.get_llm_with_tools(tools, provider=provider, **kwargs)
