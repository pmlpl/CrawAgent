"""爬虫错误分类系统

将错误分为三类，决定不同处理策略：
- Permanent (永久): 404/410/403 — 不重试，直接放弃
- Transient (瞬时): 429/500/502/503/504/超时/连接错误 — 可重试（指数退避）
- Auth (认证): 401/407 — 需要凭据/代理认证
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional


class ErrorCategory(str, Enum):
    """错误类别"""
    PERMANENT = "permanent"   # 永久错误：不重试
    TRANSIENT = "transient"   # 瞬时错误：可重试
    AUTH = "auth"             # 认证错误：需要凭据
    UNKNOWN = "unknown"       # 未知错误


# 各类别对应的状态码集合
PERMANENT_STATUS = frozenset({400, 403, 404, 405, 410, 451})
TRANSIENT_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504, 522, 524})
AUTH_STATUS = frozenset({401, 407})


def classify_http_status(status_code: int) -> ErrorCategory:
    """根据 HTTP 状态码分类错误"""
    if status_code in PERMANENT_STATUS:
        return ErrorCategory.PERMANENT
    if status_code in TRANSIENT_STATUS:
        return ErrorCategory.TRANSIENT
    if status_code in AUTH_STATUS:
        return ErrorCategory.AUTH
    if 200 <= status_code < 400:
        return ErrorCategory.UNKNOWN  # 非错误
    if 400 <= status_code < 500:
        return ErrorCategory.PERMANENT  # 其他 4xx 视为永久
    return ErrorCategory.TRANSIENT  # 其他 5xx 视为瞬时


def classify_exception(exc: BaseException) -> ErrorCategory:
    """根据异常类型分类错误

    网络超时/连接错误 → TRANSIENT
    其他 → UNKNOWN
    """
    exc_name = type(exc).__name__.lower()
    # 常见网络/超时异常
    transient_keywords = (
        "timeout", "timedout", "connecterror", "connectionerror",
        "remoteprotocolerror", "pooltimeout", "readtimeout", "writetimeout",
        "connectionreset", "connectionaborted", "connectionrefused",
        "brokenpipe", "sslerror", "retryable",
    )
    for kw in transient_keywords:
        if kw in exc_name:
            return ErrorCategory.TRANSIENT
    return ErrorCategory.UNKNOWN


def classify_error(
    status_code: Optional[int] = None,
    exc: Optional[BaseException] = None,
) -> ErrorCategory:
    """综合分类错误

    优先使用 HTTP 状态码；其次使用异常类型。
    """
    if status_code is not None:
        cat = classify_http_status(status_code)
        if cat != ErrorCategory.UNKNOWN:
            return cat
    if exc is not None:
        cat = classify_exception(exc)
        if cat != ErrorCategory.UNKNOWN:
            return cat
    return ErrorCategory.UNKNOWN


def should_retry(category: ErrorCategory, retry_count: int, max_retries: int = 3) -> bool:
    """判断是否应该重试"""
    if category == ErrorCategory.TRANSIENT:
        return retry_count < max_retries
    # 永久/认证错误不重试
    return False


def error_to_dict(
    category: ErrorCategory,
    status_code: Optional[int] = None,
    message: str = "",
    strategy: str = "",
) -> dict:
    """将错误信息转为结构化字典，供工具结果返回"""
    return {
        "category": category.value,
        "status_code": status_code,
        "message": message,
        "strategy": strategy,
        "retryable": category == ErrorCategory.TRANSIENT,
    }
