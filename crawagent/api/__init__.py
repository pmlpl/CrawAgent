"""
CrawAgent API - FastAPI 服务
"""

__all__ = ["app"]

# 延迟导入避免循环依赖
from crawagent.api.server import app

__all__ = ["app"]