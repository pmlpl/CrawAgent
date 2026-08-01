"""基线快照存储（P4-5）：保存 URL 上次抓取的内容 hash + 字段值

每次监控触发时：
1. 抓取 URL → 提取关注字段（price/stock/...）
2. 与 baseline 对比 → 检测变化
3. 更新 baseline 为本次内容
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional, Tuple

from loguru import logger

from crawagent.monitor.models import MonitorStore, MonitorTask, get_monitor_store


class BaselineStore:
    """基线快照管理器

    封装 MonitorStore 的 baseline 读写，提供：
    - get_baseline(task_id, url) → 上次的内容 hash + 字段
    - save_baseline(task_id, url, fields) → 保存本次为基线
    - compute_hash(content) → 计算内容 hash
    """

    def __init__(self, store: Optional[MonitorStore] = None):
        self.store = store or get_monitor_store()

    def get_baseline(self, task_id: str, url: str) -> Optional[Dict[str, Any]]:
        """获取上次的基线快照"""
        return self.store.get_baseline(task_id, url)

    def save_baseline(
        self,
        task_id: str,
        url: str,
        content: str,
        fields: Dict[str, Any],
    ) -> None:
        """保存当前内容为基线快照"""
        content_hash = self.compute_hash(content)
        self.store.save_baseline(
            task_id=task_id,
            url=url,
            content_hash=content_hash,
            fields=fields,
            raw_content=content[:5000],  # 只保留前 5KB 避免数据库膨胀
        )
        logger.debug(
            f"[Baseline] 保存 {url} hash={content_hash[:8]} "
            f"fields={list(fields.keys())}"
        )

    @staticmethod
    def compute_hash(content: str) -> str:
        """计算内容 hash（SHA-256，用于快速比较整体变化）"""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def compute_fields_hash(fields: Dict[str, Any]) -> str:
        """计算字段值的 hash（用于检测字段级变化）"""
        import json
        # 排序键，确保字典顺序不影响 hash
        canonical = json.dumps(fields, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
