"""模型对接层 — 通过 langchain-openai 兼容格式调用 DeepSeek API

缓存命中说明：
DeepSeek API 服务端自动对请求的最长公共前缀进行缓存（prompt caching），
无需客户端额外配置。为保证缓存命中率，需遵循以下原则：

1. SYSTEM_PROMPT 必须为纯静态字符串，不能包含时间戳/随机数等动态内容
2. 工具定义（@tool docstring）必须稳定，不能运行时修改
3. 消息顺序固定：system prompt → tool definitions → conversation history
4. 对话历史只追加不修改（append-only），确保前缀不变

满足以上条件后，system prompt + tool definitions 部分每次请求完全相同，
DeepSeek 会自动缓存这段前缀，后续请求命中缓存后该部分按缓存价格计费（大幅降低成本）。
"""
from langchain_openai import ChatOpenAI
from crawagent.config.settings import get_settings


def get_llm() -> ChatOpenAI:
    """获取 LLM 实例（DeepSeek，兼容 OpenAI 接口格式）

    Returns:
        ChatOpenAI 实例，支持 tool calling
    """
    settings = get_settings()
    return ChatOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        model=settings.default_model,
        temperature=0,  # 爬虫任务用 0 温度，确保输出稳定
        timeout=120,        # 请求超时 120 秒，避免 API 挂起导致无限等待
        max_retries=2,      # 超时自动重试 2 次
    )
