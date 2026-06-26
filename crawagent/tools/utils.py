"""常用工具集合

- User-Agent 池
- 数据导出 (JSON / CSV)
"""
from __future__ import annotations

import csv
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ============================================================
# User-Agent 池
# ============================================================

# 统一的 UA 池（合并自 utils.py 和 base_crawler.py 的重复定义）
# 包含桌面和移动浏览器 UA
_UA_POOL = [
    # Chrome on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    # Firefox on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    # Edge on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    # Chrome on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    # Safari on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    # Chrome on Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    # Mobile UA（偶尔用，显得更自然）
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
]

# 向后兼容：保留旧的变量名指向新列表
_DESKTOP_UAS = _UA_POOL


@dataclass
class UserAgentPool:
    """简单的 UA 池 —— 每次请求随机挑一个，让流量看起来像不同用户"""

    pool: list[str] = field(default_factory=lambda: list(_UA_POOL))
    _last: str = ""

    def pick(self) -> str:
        """随机挑选一个 UA（避免连续两次完全相同）"""
        if len(self.pool) == 1:
            return self.pool[0]
        ua = random.choice(self.pool)
        while ua == self._last:
            ua = random.choice(self.pool)
        self._last = ua
        return ua

    def add(self, ua: str) -> None:
        if ua and ua not in self.pool:
            self.pool.append(ua)


# 全局单例
_global_ua_pool = UserAgentPool()


def random_user_agent() -> str:
    """便捷函数：获取随机 UA"""
    return _global_ua_pool.pick()


# ============================================================
# 数据导出
# ============================================================

def export_json(data: list[dict[str, Any]] | dict[str, Any], file_path: str | Path) -> Path:
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def export_csv(data: list[dict[str, Any]], file_path: str | Path) -> Path:
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not data:
        path.write_text("", encoding="utf-8")
        return path

    fieldnames = list(data[0].keys())
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in data:
            writer.writerow({k: row.get(k, "") for k in fieldnames})
    return path


def export_markdown(data: list[dict[str, Any]], title: str, file_path: str | Path) -> Path:
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = [f"# {title}", ""]
    if not data:
        lines.append("_无数据_")
    else:
        headers = list(data[0].keys())
        # Markdown 表格
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join("---" for _ in headers) + " |")
        for row in data:
            # 转义可能的换行和 | 字符
            vals = [str(row.get(h, "")).replace("\n", " ").replace("|", "\\|") for h in headers]
            lines.append("| " + " | ".join(vals) + " |")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path
