"""LLM 封装层 —— 统一的多模型接入

Provider: openai (DeepSeek / OpenAI / LM Studio 等)  或  ollama
配置统一在 models.json 中管理。
"""
from .factory import LLMFactory, to_chat_messages

__all__ = ["LLMFactory", "to_chat_messages"]
