"""URL 过滤器链（P6-5）

参考 Crawl4AI deep_crawling/filters.py 设计：
- 多个 URLFilter 按顺序执行，任一拒绝则 URL 被过滤
- 内置常见过滤器：域名白/黑名单、路径正则、内容类型、robots、重复参数、最大深度等
- 支持自定义扩展（继承 URLFilter，重写 accept(url)）
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Pattern, Set
from urllib.parse import urlparse, parse_qs, urlunparse, urlencode


# ---------------------------------------------------------------------------
# URL 规范化（去重前置步骤，非 Filter，作为 FilterChain 前置）
# ---------------------------------------------------------------------------

def normalize_url(url: str, strip_utm: bool = True, strip_fragment: bool = True) -> str:
    """规范化 URL：统一协议小写、去默认端口、排序 query、去 UTM、去锚点。

    用于深度爬取时的指纹去重，不同顺序参数视为同一 URL。
    """
    if not url:
        return url
    try:
        parsed = urlparse(url)
        scheme = parsed.scheme.lower() or "https"
        netloc = parsed.netloc.lower()
        # 去默认端口
        if scheme == "http" and netloc.endswith(":80"):
            netloc = netloc[:-3]
        elif scheme == "https" and netloc.endswith(":443"):
            netloc = netloc[:-4]
        # 处理 query
        qs = parse_qs(parsed.query, keep_blank_values=True)
        if strip_utm:
            qs = {k: v for k, v in qs.items() if not k.lower().startswith("utm_") and k.lower() not in {"ref", "ref_src"}}
        query_items = sorted(qs.items())
        flat_query = []
        for k, vs in query_items:
            for v in sorted(vs):
                flat_query.append((k, v))
        query = urlencode(flat_query, doseq=True)
        path = parsed.path or "/"
        fragment = "" if strip_fragment else parsed.fragment
        return urlunparse((scheme, netloc, path, "", query, fragment))
    except Exception:
        return url


# ---------------------------------------------------------------------------
# Filter 基类 + 内置实现
# ---------------------------------------------------------------------------

class URLFilter:
    """URL 过滤器基类。

    accept(url) 返回 True=放行，False=过滤（拒绝）。
    reject_reason 用于调试日志。
    """

    name: str = "base"

    def accept(self, url: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        return True

    def __call__(self, url: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        return self.accept(url, metadata)


class SameDomainFilter(URLFilter):
    """只保留种子域名（允许子域名）。"""
    name = "same_domain"

    def __init__(self, seed_url: str, allow_subdomain: bool = True) -> None:
        parsed = urlparse(seed_url)
        self.seed_netloc = parsed.netloc.lower()
        self.allow_subdomain = allow_subdomain
        # 去端口后的主机
        self.seed_host = self.seed_netloc.split(":")[0]

    def accept(self, url: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        try:
            host = urlparse(url).netloc.lower().split(":")[0]
        except Exception:
            return False
        if host == self.seed_host:
            return True
        if self.allow_subdomain and host.endswith("." + self.seed_host):
            return True
        return False


class DomainWhitelistFilter(URLFilter):
    """域名白名单（列表）。"""
    name = "domain_whitelist"

    def __init__(self, domains: List[str], allow_subdomain: bool = True) -> None:
        self.domains: Set[str] = {d.lower().split(":")[0] for d in domains}
        self.allow_subdomain = allow_subdomain

    def accept(self, url: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        try:
            host = urlparse(url).netloc.lower().split(":")[0]
        except Exception:
            return False
        for d in self.domains:
            if host == d:
                return True
            if self.allow_subdomain and host.endswith("." + d):
                return True
        return False


class DomainBlacklistFilter(URLFilter):
    """域名黑名单。"""
    name = "domain_blacklist"

    def __init__(self, domains: List[str]) -> None:
        self.domains: Set[str] = {d.lower().split(":")[0] for d in domains}

    def accept(self, url: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        try:
            host = urlparse(url).netloc.lower().split(":")[0]
        except Exception:
            return True  # 解析不出按放行
        for d in self.domains:
            if host == d or host.endswith("." + d):
                return False
        return True


class PathRegexFilter(URLFilter):
    """路径正则过滤。"""
    name = "path_regex"

    def __init__(
        self,
        include: Optional[List[str]] = None,
        exclude: Optional[List[str]] = None,
    ) -> None:
        self._include: Optional[List[Pattern]] = [re.compile(p) for p in include] if include else None
        self._exclude: Optional[List[Pattern]] = [re.compile(p) for p in exclude] if exclude else None

    def accept(self, url: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        try:
            path = urlparse(url).path or "/"
        except Exception:
            return True
        if self._include:
            if not any(p.search(path) for p in self._include):
                return False
        if self._exclude:
            if any(p.search(path) for p in self._exclude):
                return False
        return True


class ExtensionFilter(URLFilter):
    """文件扩展名白名单/黑名单过滤。"""
    name = "extension"

    DEFAULT_BLACKLIST_EXTS = {
        ".pdf", ".zip", ".rar", ".7z", ".tar", ".gz",
        ".mp3", ".mp4", ".avi", ".mov", ".wmv",
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico",
        ".css", ".js", ".map", ".woff", ".woff2", ".ttf", ".eot",
        ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".exe", ".msi",
    }

    def __init__(
        self,
        allow_ext: Optional[Set[str]] = None,
        block_ext: Optional[Set[str]] = None,
    ) -> None:
        self.allow_ext = {e.lower() for e in allow_ext} if allow_ext else None
        self.block_ext = {e.lower() for e in (block_ext or self.DEFAULT_BLACKLIST_EXTS)}

    def accept(self, url: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        try:
            path = urlparse(url).path.lower()
        except Exception:
            return True
        # 从路径尾取扩展名
        dot = path.rfind(".")
        if dot == -1:
            ext = ""
        else:
            slash = path.rfind("/")
            if dot < slash:
                ext = ""
            else:
                ext = path[dot:]
        if self.allow_ext is not None:
            if ext and ext not in self.allow_ext:
                return False
        if self.block_ext:
            if ext in self.block_ext:
                return False
        return True


class MaxDepthFilter(URLFilter):
    """按深度过滤（metadata 里 depth 字段）。"""
    name = "max_depth"

    def __init__(self, max_depth: int = 3) -> None:
        self.max_depth = max_depth

    def accept(self, url: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        depth = (metadata or {}).get("depth") or 0
        try:
            depth = int(depth)
        except Exception:
            depth = 0
        return depth <= self.max_depth


class QueryParamLimitFilter(URLFilter):
    """最大查询参数数过滤（防无限排列组合：?a=1&b=2&c=3...）。"""
    name = "query_param_limit"

    def __init__(self, max_params: int = 10) -> None:
        self.max_params = max_params

    def accept(self, url: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        try:
            qs = parse_qs(urlparse(url).query, keep_blank_values=True)
        except Exception:
            return True
        return len(qs) <= self.max_params


class DuplicateQueryParamFilter(URLFilter):
    """重复参数（不同值）指纹去重。

    如 /?page=1&page=2 等，通过统计唯一参数名集合做指纹。
    真正的指纹去重在 deep_crawl 内部通过 normalize_url + seen set 完成，
    这里只过滤"纯重复/垃圾参数"型 URL。
    """
    name = "dup_query_param"

    def __init__(self, max_values_per_param: int = 1) -> None:
        self.max_values_per_param = max_values_per_param

    def accept(self, url: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        try:
            qs = parse_qs(urlparse(url).query, keep_blank_values=True)
        except Exception:
            return True
        for _k, vs in qs.items():
            if len(vs) > self.max_values_per_param:
                return False
        return True


class UrlLengthFilter(URLFilter):
    """URL 长度上限过滤（防超长动态路径崩溃）。"""
    name = "url_length"

    def __init__(self, max_length: int = 2048) -> None:
        self.max_length = max_length

    def accept(self, url: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        return len(url) <= self.max_length


class CustomFilter(URLFilter):
    """自定义 predicate 过滤。"""
    name = "custom"

    def __init__(
        self,
        predicate: Callable[[str, Optional[Dict[str, Any]]], bool],
        name: str = "custom",
    ) -> None:
        self._pred = predicate
        self.name = name

    def accept(self, url: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        return bool(self._pred(url, metadata))


# ---------------------------------------------------------------------------
# FilterChain
# ---------------------------------------------------------------------------

@dataclass
class FilterResult:
    """单个 URL 的过滤结果。"""
    url: str
    accepted: bool
    rejected_by: str = ""  # 拒绝时的过滤器名
    duration_ms: float = 0.0


class FilterChain:
    """过滤器链：顺序执行所有 Filter，任一拒绝则整体拒绝。

    用法：
        chain = FilterChain([
            SameDomainFilter(seed_url),
            MaxDepthFilter(max_depth=3),
            ExtensionFilter(),
        ])
        ok = chain.accept(url, {"depth": 2})
        results = chain.accept_many([url1, url2])
    """

    def __init__(self, filters: List[URLFilter] = None) -> None:
        self._filters: List[URLFilter] = list(filters or [])
        self._stats: Dict[str, int] = {}  # filter_name → rejected count

    # ---- 管理 ----

    def add(self, f: URLFilter, index: Optional[int] = None) -> "FilterChain":
        if index is None or index >= len(self._filters):
            self._filters.append(f)
        else:
            self._filters.insert(index, f)
        return self

    def remove(self, name: str) -> bool:
        for i, f in enumerate(self._filters):
            if f.name == name:
                del self._filters[i]
                return True
        return False

    def list_filters(self) -> List[str]:
        return [f.name for f in self._filters]

    @property
    def stats(self) -> Dict[str, int]:
        return dict(self._stats)

    def reset_stats(self) -> None:
        self._stats.clear()

    # ---- 判断 ----

    def accept(self, url: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        for f in self._filters:
            try:
                ok = f.accept(url, metadata)
            except Exception:
                ok = True  # 过滤器异常不阻塞抓取
            if not ok:
                self._stats[f.name] = self._stats.get(f.name, 0) + 1
                return False
        return True

    def accept_one(self, url: str, metadata: Optional[Dict[str, Any]] = None) -> FilterResult:
        """单条详细结果（返回拒绝者名称）。"""
        t0 = time.perf_counter()
        for f in self._filters:
            try:
                ok = f.accept(url, metadata)
            except Exception:
                ok = True
            if not ok:
                ms = (time.perf_counter() - t0) * 1000
                self._stats[f.name] = self._stats.get(f.name, 0) + 1
                return FilterResult(url=url, accepted=False, rejected_by=f.name, duration_ms=ms)
        ms = (time.perf_counter() - t0) * 1000
        return FilterResult(url=url, accepted=True, duration_ms=ms)

    def accept_many(
        self,
        items: List[Any],
        metadata_fn: Callable[[Any], Optional[Dict[str, Any]]] = None,
        url_fn: Callable[[Any], str] = None,
    ) -> List[FilterResult]:
        """批量过滤。items 可为 URL 字符串列表或包含 url/metadata 的对象列表。"""
        results: List[FilterResult] = []
        for item in items:
            if url_fn is not None:
                url = url_fn(item)
                md = metadata_fn(item) if metadata_fn else None
            elif isinstance(item, str):
                url = item
                md = None
            elif isinstance(item, dict):
                url = item.get("url", "")
                md = {k: v for k, v in item.items() if k != "url"}
            else:
                url = getattr(item, "url", "")
                md = None
            results.append(self.accept_one(url, md))
        return results
