"""变化检测（P4-3）：对比基线与当前内容，生成结构化 diff

支持三种变化检测：
1. 整体内容 hash 变化（最粗粒度）
2. 字段级变化（price: 7999 → 8999）
3. 元素文本变化（CSS 选择器定位的元素）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from crawagent.monitor.baseline import BaselineStore


@dataclass
class FieldChange:
    """单个字段的变化"""
    field: str
    old_value: Any
    new_value: Any
    change_type: str  # "changed" / "added" / "removed"
    # 数值变化幅度（仅当新旧值都可解析为数字时有效）
    delta: Optional[float] = None        # 新值 - 旧值
    percent_change: Optional[float] = None  # 百分比变化（%），如 -15.0 表示下降 15%

    def _num(self, v: Any) -> Optional[float]:
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).strip().replace(",", "").replace("¥", "").replace("$", "").replace("￥", "").replace("%", "")
        try:
            return float(s)
        except (ValueError, TypeError):
            return None

    def __post_init__(self) -> None:
        """自动计算数值变化幅度（新旧值均可解析为数字时）。"""
        if self.delta is None or self.percent_change is None:
            old_n = self._num(self.old_value)
            new_n = self._num(self.new_value)
            if old_n is not None and new_n is not None:
                if self.delta is None:
                    self.delta = round(new_n - old_n, 4)
                if self.percent_change is None:
                    if old_n == 0:
                        self.percent_change = None if new_n == 0 else 100.0
                    else:
                        self.percent_change = round((new_n - old_n) / abs(old_n) * 100, 2)


@dataclass
class DiffResult:
    """变化检测结果"""
    has_changed: bool
    content_hash_changed: bool
    field_changes: List[FieldChange] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "has_changed": self.has_changed,
            "content_hash_changed": self.content_hash_changed,
            "field_changes": [
                {
                    "field": c.field,
                    "old_value": str(c.old_value),
                    "new_value": str(c.new_value),
                    "change_type": c.change_type,
                    "delta": c.delta,
                    "percent_change": c.percent_change,
                }
                for c in self.field_changes
            ],
            "summary": self.summary,
        }

    @property
    def max_change_percent(self) -> Optional[float]:
        """所有字段变化的百分比绝对值最大值（用于阈值过滤）。"""
        pcts = [abs(c.percent_change) for c in self.field_changes if c.percent_change is not None]
        return max(pcts) if pcts else None


class DiffDetector:
    """变化检测器

    用法：
        detector = DiffDetector()
        result = detector.compare(
            old_hash="abc123",
            new_hash="def456",
            old_fields={"price": "7999"},
            new_fields={"price": "8999"},
        )
    """

    def __init__(self, baseline_store: Optional[BaselineStore] = None):
        self.baseline = baseline_store or BaselineStore()

    def compare(
        self,
        old_hash: str,
        new_hash: str,
        old_fields: Dict[str, Any],
        new_fields: Dict[str, Any],
        watch_fields: Optional[List[str]] = None,
    ) -> DiffResult:
        """对比基线与当前内容

        Args:
            old_hash: 上次内容 hash
            new_hash: 本次内容 hash
            old_fields: 上次字段值
            new_fields: 本次字段值
            watch_fields: 只监控这些字段的变化（空表示监控所有字段）

        Returns:
            DiffResult
        """
        content_changed = old_hash != new_hash
        field_changes: List[FieldChange] = []

        # 字段对比
        all_fields = set(old_fields.keys()) | set(new_fields.keys())
        watch_set = set(watch_fields) if watch_fields else all_fields
        for fname in sorted(all_fields):
            if fname not in watch_set:
                continue
            old_v = old_fields.get(fname)
            new_v = new_fields.get(fname)
            if old_v is None and new_v is not None:
                field_changes.append(FieldChange(fname, None, new_v, "added"))
            elif old_v is not None and new_v is None:
                field_changes.append(FieldChange(fname, old_v, None, "removed"))
            elif str(old_v) != str(new_v):
                field_changes.append(FieldChange(fname, old_v, new_v, "changed"))

        has_changed = content_changed or bool(field_changes)
        summary = self._build_summary(content_changed, field_changes)

        return DiffResult(
            has_changed=has_changed,
            content_hash_changed=content_changed,
            field_changes=field_changes,
            summary=summary,
        )

    def _build_summary(
        self,
        content_changed: bool,
        field_changes: List[FieldChange],
    ) -> str:
        """生成人类可读的变化摘要"""
        parts: List[str] = []
        if content_changed:
            parts.append("内容已更新")
        for c in field_changes:
            if c.change_type == "changed":
                parts.append(f"{c.field}: {c.old_value} → {c.new_value}")
            elif c.change_type == "added":
                parts.append(f"{c.field}: 新增（{c.new_value}）")
            elif c.change_type == "removed":
                parts.append(f"{c.field}: 已消失（原值 {c.old_value}）")
        if not parts:
            return "无变化"
        return "；".join(parts)

    def compare_with_baseline(
        self,
        task: MonitorTask,
        new_content: str,
        new_fields: Dict[str, Any],
    ) -> tuple:
        """与数据库中的基线对比

        Args:
            task: 监控任务
            new_content: 本次抓取内容
            new_fields: 本次提取字段

        Returns:
            (DiffResult, old_baseline_or_None)
        """
        from crawagent.monitor.models import MonitorTask
        old = self.baseline.get_baseline(task.id, task.url)
        new_hash = self.baseline.compute_hash(new_content)

        if old is None:
            # 首次运行，无基线
            result = DiffResult(
                has_changed=False,
                content_hash_changed=True,
                field_changes=[],
                summary="首次运行，已建立基线",
            )
            return result, None

        old_hash = old.get("content_hash", "")
        old_fields = old.get("fields", {})
        result = self.compare(
            old_hash=old_hash,
            new_hash=new_hash,
            old_fields=old_fields,
            new_fields=new_fields,
            watch_fields=task.watch_fields,
        )
        return result, old
