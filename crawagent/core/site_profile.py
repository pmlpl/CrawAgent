"""网站画像系统：记录每个网站的特征，支持针对性优化和持续训练。

用途：
- 自动发现网站技术栈（SPA/SSR/静态）
- 记录有效路由和导航结构
- 保存图片加载模式、反爬等级等信息
- 人工标注备注，不断积累每个网站的爬取经验
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict, fields
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from loguru import logger


@dataclass
class SiteProfile:
    """网站画像：记录一个网站的特征，用于后续针对性优化。

    字段说明：
        domain: 域名，如 "haowallpaper.com"
        spa_type: SPA 框架类型，如 "nuxt"/"vue"/"react"/"next"/""
        site_framework: 完整框架名，如 "Nuxt 3"/"Next.js 14"
        known_routes: 已知有效路由列表，如 ["/homeView", "/mobileView"]
        image_loading: 图片加载方式，static(直接src)/lazy(懒加载)/dynamic(动态JS)
        anti_bot_level: 反爬等级，none/low/medium/high
        uses_client_routing: 是否使用客户端路由
        nav_links: 导航链接 [{text, href, group}]
        notes: 人工备注，由用户提供针对性优化建议
        crawl_count: 爬取次数
        last_updated: 最后更新时间戳
    """

    domain: str
    spa_type: str = ""
    site_framework: str = ""
    known_routes: List[str] = field(default_factory=list)
    image_loading: str = "static"
    anti_bot_level: str = "none"
    uses_client_routing: bool = False
    nav_links: List[Dict[str, str]] = field(default_factory=list)
    notes: str = ""
    crawl_count: int = 0
    last_updated: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典"""
        d = asdict(self)
        d["last_updated"] = self.last_updated or time.time()
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SiteProfile":
        """从字典反序列化"""
        # 过滤掉未知字段，保持兼容性
        known = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    @classmethod
    async def discover(cls, url: str, html: str) -> "SiteProfile":
        """从页面内容自动发现网站特征。

        自动检测：
        - SPA 框架 (Nuxt/Vue/React/Next.js/Angular)
        - 图片加载方式 (static/lazy)
        - 客户端路由
        - 导航链接
        """
        domain = urlparse(url).netloc.lower()
        domain = domain.replace("www.", "")
        profile = cls(domain=domain)

        if not html:
            return profile

        # 1. 检测 SPA 框架
        _html = html.lower()
        if "__nuxt__" in _html or "_nuxt" in html:
            profile.spa_type = "nuxt"
            profile.site_framework = "Nuxt"
            profile.uses_client_routing = True
        elif "__next_data__" in _html:
            profile.spa_type = "next"
            profile.site_framework = "Next.js"
            profile.uses_client_routing = True
        elif "data-n-head" in html:
            profile.spa_type = "vue"
            profile.site_framework = "Vue SSR"
            profile.uses_client_routing = True
        elif "vue-router" in _html or "vuex" in _html:
            profile.spa_type = "vue"
            profile.site_framework = "Vue SPA"
            profile.uses_client_routing = True
        elif "react-router" in _html:
            profile.spa_type = "react"
            profile.site_framework = "React SPA"
            profile.uses_client_routing = True
        elif "ng-version" in _html or "angular" in _html:
            profile.spa_type = "angular"
            profile.site_framework = "Angular"
            profile.uses_client_routing = True

        # 2. 检测图片加载方式
        from bs4 import BeautifulSoup
        try:
            soup = BeautifulSoup(html, "html.parser")
            imgs = soup.find_all("img")
            if imgs:
                lazy_count = 0
                for img in imgs:
                    attrs = img.attrs or {}
                    has_lazy_attr = any(
                        k in attrs for k in
                        ("data-src", "data-original", "data-lazy", "data-srcset", "data-url")
                    )
                    is_lazy_loading = str(attrs.get("loading", "")).lower() == "lazy"
                    if has_lazy_attr or is_lazy_loading:
                        lazy_count += 1
                lazy_ratio = lazy_count / len(imgs)
                if lazy_ratio > 0.5:
                    profile.image_loading = "lazy"
                elif lazy_ratio > 0:
                    profile.image_loading = "dynamic"
                else:
                    profile.image_loading = "static"
        except Exception:
            pass

        # 3. 检测反爬等级
        anti_bot_signals = 0
        # 3.1 Cloudflare / JS 挑战
        if "cloudflare" in _html or "cf-challenge" in _html or "cf-ray" in _html:
            anti_bot_signals += 2
        # 3.2 验证码
        if "captcha" in _html or "verify" in _html:
            anti_bot_signals += 2
        # 3.3 反爬 JS 检测
        if "webdriver" in _html or "headless" in _html or "phantom" in _html:
            anti_bot_signals += 1
        # 3.4 请求频率限制
        if "rate limit" in _html or "too many requests" in _html:
            anti_bot_signals += 2
        # 3.5 页面指纹
        if "fingerprint" in _html or "security" in _html.lower():
            anti_bot_signals += 1

        if anti_bot_signals >= 3:
            profile.anti_bot_level = "high"
        elif anti_bot_signals >= 2:
            profile.anti_bot_level = "medium"
        elif anti_bot_signals >= 1:
            profile.anti_bot_level = "low"
        else:
            profile.anti_bot_level = "none"

        # 4. 提取导航链接（从 nav/header/ul 等元素）
        try:
            nav_links = []
            seen_hrefs = set()
            # 优先找 nav 和 header 中的链接
            nav_containers = soup.find_all(["nav", "header", "menu"]) or []
            if not nav_containers:
                # 兜底：找 ul 或 div.menu
                nav_containers = soup.select("ul.nav, ul.menu, div.menu, div.nav")
            for container in nav_containers:
                for a in container.find_all("a", href=True):
                    href = a["href"].strip()
                    if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                        continue
                    text = a.get_text(strip=True)
                    if not text or len(text) > 20:
                        continue
                    if href not in seen_hrefs:
                        seen_hrefs.add(href)
                        nav_links.append({
                            "text": text,
                            "href": href,
                            "group": container.name or "nav",
                        })
            # 如果 nav 容器中没找到，从所有 a 标签中提取（排除 footer/外链）
            if not nav_links:
                for a in soup.find_all("a", href=True):
                    href = a["href"].strip()
                    if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "http")):
                        continue
                    text = a.get_text(strip=True)
                    if not text or len(text) > 20 or len(text) < 2:
                        continue
                    if href not in seen_hrefs:
                        seen_hrefs.add(href)
                        nav_links.append({
                            "text": text,
                            "href": href,
                            "group": "general",
                        })
            # 只取前 50 条，避免数据量太大
            profile.nav_links = nav_links[:50]
        except Exception:
            pass

        profile.last_updated = time.time()
        return profile


class SiteProfileStore:
    """网站画像存储（文件版 JSON）。

    所有画像保存在 store_dir 目录下，每个域名一个 JSON 文件。
    支持 CRUD 和搜索。
    """

    def __init__(self, store_dir: str = "./profiles"):
        self._store_dir = os.path.abspath(store_dir)
        os.makedirs(self._store_dir, exist_ok=True)

    def _profile_path(self, domain: str) -> str:
        """获取画像文件路径"""
        # 域名安全转义（防止路径穿越）
        safe = domain.replace(".", "_").replace("/", "_").replace(":", "_")
        return os.path.join(self._store_dir, f"{safe}.json")

    def get(self, domain: str) -> Optional[SiteProfile]:
        """获取域名画像，不存在返回 None"""
        path = self._profile_path(domain)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return SiteProfile.from_dict(data)
        except Exception as e:
            logger.debug(f"读取画像失败 {domain}: {e}")
            return None

    def save(self, profile: SiteProfile) -> None:
        """保存画像"""
        profile.last_updated = time.time()
        path = self._profile_path(profile.domain)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(profile.to_dict(), f, ensure_ascii=False, indent=2)
            logger.debug(f"网站画像已保存: {profile.domain} -> {path}")
        except Exception as e:
            logger.warning(f"保存画像失败 {profile.domain}: {e}")

    def delete(self, domain: str) -> bool:
        """删除画像"""
        path = self._profile_path(domain)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False

    def list_all(self) -> List[SiteProfile]:
        """列出所有已保存的画像"""
        profiles = []
        if not os.path.isdir(self._store_dir):
            return profiles
        for fname in os.listdir(self._store_dir):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(self._store_dir, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                profiles.append(SiteProfile.from_dict(data))
            except Exception:
                continue
        return profiles

    def search_by_note(self, keyword: str) -> List[SiteProfile]:
        """按备注关键词搜索画像"""
        keyword = keyword.lower()
        return [p for p in self.list_all() if keyword in p.notes.lower()]

    def get_or_discover(
        self, domain: str, url: str = "", html: str = ""
    ) -> SiteProfile:
        """获取已有画像，如果没有则自动发现创建。

        Args:
            domain: 域名
            url: 页面 URL（用于自动发现）
            html: 页面 HTML（用于自动发现）
        """
        profile = self.get(domain)
        if profile is None:
            profile = SiteProfile(domain=domain)
            if html:
                # 异步版本见 async_get_or_discover
                pass
        return profile

    async def async_get_or_discover(
        self, domain: str, url: str = "", html: str = ""
    ) -> SiteProfile:
        """异步版本：获取或自动发现创建画像"""
        profile = self.get(domain)
        if profile is not None:
            profile.crawl_count += 1
            self.save(profile)
            return profile

        # 自动发现
        profile = await SiteProfile.discover(url or f"https://{domain}", html or "")
        # 标记为首次
        if not profile.crawl_count:
            profile.crawl_count = 1
        self.save(profile)
        return profile

    def update_notes(self, domain: str, notes: str) -> Optional[SiteProfile]:
        """更新人工备注（由用户提供针对性优化建议）"""
        profile = self.get(domain)
        if profile is None:
            return None
        profile.notes = notes
        self.save(profile)
        return profile

    def add_route(self, domain: str, route: str) -> Optional[SiteProfile]:
        """添加已知有效路由"""
        profile = self.get(domain)
        if profile is None:
            return None
        if route not in profile.known_routes:
            profile.known_routes.append(route)
            self.save(profile)
        return profile

    def add_route_batch(self, domain: str, routes: List[str]) -> Optional[SiteProfile]:
        """批量添加已知有效路由"""
        profile = self.get(domain)
        if profile is None:
            return None
        for r in routes:
            if r not in profile.known_routes:
                profile.known_routes.append(r)
        self.save(profile)
        return profile


# 全局单例（懒加载）
_default_store: Optional[SiteProfileStore] = None


def get_profile_store(store_dir: str = "./profiles") -> SiteProfileStore:
    """获取全局默认画像存储实例"""
    global _default_store
    if _default_store is None:
        _default_store = SiteProfileStore(store_dir=store_dir)
    return _default_store