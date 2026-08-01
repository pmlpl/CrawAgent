from __future__ import annotations

import re
import json
import asyncio
from typing import Any, Dict, Iterator, List, Optional

from langchain_core.callbacks import CallbackManagerForLLMRun, AsyncCallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class MockLLM(BaseChatModel):
    """模拟 LLM，用于测试，不消耗 token"""

    model: str = "mock-model"
    temperature: float = 0.0
    delay: float = 0.3

    @property
    def _llm_type(self) -> str:
        return "mock"

    def bind_tools(self, tools, **kwargs):
        """Mock: 接受工具绑定但不实际调用（避免 mock 模式崩溃）"""
        return self.bind(bound_tools=tools)

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        content = self._mock_response(messages)
        message = AIMessage(content=content)
        generation = ChatGeneration(message=message)
        return ChatResult(generations=[generation])

    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        await asyncio.sleep(self.delay)
        return self._generate(messages, stop=stop, **kwargs)

    def _mock_response(self, messages: List[BaseMessage]) -> str:
        """根据消息内容智能返回模拟响应"""
        if not messages:
            return ""
        
        last_msg = messages[-1]
        content = last_msg.content if isinstance(last_msg, BaseMessage) else str(last_msg)
        all_text = " ".join(str(m.content) for m in messages).lower()

        # 1. 页面分类
        if "分类" in all_text or "classify" in all_text or "page_type" in all_text or "页面类型" in all_text:
            return self._mock_classify(content)

        # 2. 爬取规划
        if "规划" in all_text or "plan" in all_text or "爬取策略" in all_text:
            return self._mock_plan(content)

        # 3. 站点分析
        if "分析" in all_text or "analyze" in all_text or "site" in all_text:
            return self._mock_analyze(content)

        # 4. 提取数据
        if "提取" in all_text or "extract" in all_text or "fields" in all_text:
            return self._mock_extract(content)

        # 5. 反爬检测
        if "反爬" in all_text or "captcha" in all_text or "bot" in all_text:
            return self._mock_anti_bot(content)

        # 默认：自然语言回复（普通对话场景）
        return (
            "好的，我已经收到你的请求。"
            "（当前处于 Mock 模式，这只是模拟回复。"
            "如需真实 AI 回复，请更换有效的 API Key 并关闭 Mock 模式。）"
        )

    def _mock_classify(self, content: str) -> str:
        """模拟页面分类"""
        content_lower = content.lower()
        
        if any(k in content_lower for k in ["video", "视频"]):
            page_type = "video_list"
        elif any(k in content_lower for k in ["article", "detail", "show", "view", "item", "look", "product", "详情"]):
            page_type = "article"
        elif any(k in content_lower for k in ["search", "搜索"]):
            page_type = "search_result"
        elif any(k in content_lower for k in ["list", "列表", "category", "tag", "index", "home"]):
            page_type = "product_list"
        else:
            page_type = "unknown"

        return json.dumps({
            "page_type": page_type,
            "confidence": 0.95,
            "reasoning": "Mock classification result"
        }, ensure_ascii=False)

    def _mock_plan(self, content: str) -> str:
        """模拟爬取规划"""
        # 尝试从内容中提取用户设置的参数
        max_pages = 10
        max_depth = 2
        
        pages_match = re.search(r'max_pages[":\s]+(\d+)', content)
        if pages_match:
            max_pages = int(pages_match.group(1))
        
        depth_match = re.search(r'max_depth[":\s]+(\d+)', content)
        if depth_match:
            max_depth = int(depth_match.group(1))

        return json.dumps({
            "seed_urls": [],
            "target_schema": {
                "title": {"type": "str", "description": "标题"},
                "url": {"type": "str", "description": "链接"},
                "image": {"type": "str", "description": "图片"},
                "content": {"type": "str", "description": "内容摘要"}
            },
            "max_pages": max_pages,
            "max_depth": max_depth,
            "per_domain_rate": 0.5,
            "require_login": False,
            "strategy": "breadth_first",
            "extract_mode": "auto"
        }, ensure_ascii=False)

    def _mock_analyze(self, content: str) -> str:
        """模拟站点分析"""
        return json.dumps({
            "page_type": "list",
            "data_source": "html",
            "pagination": {
                "type": "query_param",
                "param": "page",
                "max_page": 10
            },
            "selectors": {
                "list_container": ".list-container, .article-list",
                "item": ".list-item, .article-item",
                "title": ".title, h3",
                "url": "a",
                "extra": {
                    "image": "img",
                    "date": ".date",
                    "author": ".author"
                }
            },
            "anti_bot": {
                "has_captcha": False,
                "require_login": False,
                "rate_limited": False,
                "notes": []
            },
            "quality_score": 85
        }, ensure_ascii=False)

    def _mock_extract(self, content: str) -> str:
        """模拟数据提取"""
        return json.dumps({
            "items": [
                {"title": "示例标题1", "url": "https://example.com/1", "image": "https://example.com/img1.jpg"},
                {"title": "示例标题2", "url": "https://example.com/2", "image": "https://example.com/img2.jpg"},
                {"title": "示例标题3", "url": "https://example.com/3", "image": "https://example.com/img3.jpg"},
            ],
            "confidence": 0.9
        }, ensure_ascii=False)

    def _mock_anti_bot(self, content: str) -> str:
        """模拟反爬检测"""
        return json.dumps({
            "is_blocked": False,
            "block_type": None,
            "recommendation": "正常访问即可",
            "requires_browser": False,
            "requires_proxy": False
        }, ensure_ascii=False)

    @property
    def _identifying_params(self) -> Dict[str, Any]:
        return {"model": self.model}
