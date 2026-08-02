"""Playwright 登录态持久化管理

使用 Playwright 的 persistent_context 实现：
- cookie / localStorage / sessionStorage 持久化
- 多站点独立 profile 管理
- 交互式登录向导（打开浏览器让用户手动登录，保存登录态）

参考 Firecrawl browser-sessions.ts。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from loguru import logger

try:
    from playwright.async_api import async_playwright, BrowserContext
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


class ProfileManager:
    """登录态持久化管理器

    为每个域名维护独立的浏览器 profile（user_data_dir），
    保存 cookie / localStorage / sessionStorage，实现登录态跨会话复用。

    用法:
        manager = ProfileManager()

        # 交互式登录（打开浏览器让用户登录）
        await manager.login_interactive("https://example.com/login")

        # 后续使用已有 profile 抓取（自动带上 cookie）
        result = await engine.fetch("https://example.com/protected",
                                     user_data_dir=manager.get_profile_dir("example.com"))
    """

    def __init__(self, base_dir: str = "~/.crawagent/profiles"):
        self.base_dir = Path(os.path.expanduser(base_dir))
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._metadata_file = self.base_dir / "profiles.json"
        self._metadata: Dict[str, Dict[str, Any]] = self._load_metadata()

    def _load_metadata(self) -> Dict[str, Dict[str, Any]]:
        """加载 profile 元数据"""
        if self._metadata_file.exists():
            try:
                return json.loads(self._metadata_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save_metadata(self) -> None:
        """保存 profile 元数据"""
        try:
            self._metadata_file.write_text(
                json.dumps(self._metadata, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"Failed to save profile metadata: {e}")

    def _domain_key(self, url: str) -> str:
        """从 URL 提取域名作为 profile key"""
        parsed = urlparse(url)
        return parsed.netloc.lower()

    def get_profile_dir(self, url: str) -> str:
        """获取域名的 profile 目录路径"""
        domain = self._domain_key(url)
        profile_dir = self.base_dir / domain
        profile_dir.mkdir(parents=True, exist_ok=True)
        return str(profile_dir)

    def has_profile(self, url: str) -> bool:
        """检查是否已有该域名的登录态"""
        domain = self._domain_key(url)
        return domain in self._metadata and self._metadata[domain].get("cookies_count", 0) > 0

    def list_profiles(self) -> List[Dict[str, Any]]:
        """列出所有已保存的 profile"""
        result = []
        for domain, meta in self._metadata.items():
            result.append({
                "domain": domain,
                **meta,
            })
        return result

    async def login_interactive(
        self,
        url: str,
        *,
        headless: bool = False,
        wait_for_navigation: str = "",
        timeout: float = 120.0,
    ) -> Dict[str, Any]:
        """交互式登录：打开浏览器让用户手动登录，保存登录态

        Args:
            url: 登录页面 URL
            headless: 是否无头模式（登录时通常需要 False 让用户操作）
            wait_for_navigation: 登录成功后等待跳转到的 URL 片段
            timeout: 等待登录的超时时间（秒）

        Returns:
            Dict: 登录结果（success, cookies_count, domain）
        """
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError("Playwright not installed")

        domain = self._domain_key(url)
        profile_dir = self.get_profile_dir(url)

        logger.info(f"[ProfileManager] 启动交互式登录: {url}")
        logger.info(f"[ProfileManager] Profile 目录: {profile_dir}")
        logger.info(f"[ProfileManager] 请在浏览器中完成登录，登录成功后关闭浏览器即可保存")

        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                profile_dir,
                headless=headless,
                args=["--disable-blink-features=AutomationControlled"],
            )

            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(url, wait_until="domcontentloaded")

            # 如果指定了登录成功后的跳转 URL，等待跳转
            if wait_for_navigation:
                try:
                    await page.wait_for_url(
                        f"**{wait_for_navigation}**",
                        timeout=int(timeout * 1000),
                    )
                    logger.info(f"[ProfileManager] 检测到登录成功跳转: {wait_for_navigation}")
                except Exception:
                    # 等待用户关闭浏览器
                    logger.info("[ProfileManager] 等待用户关闭浏览器...")
                    try:
                        await page.wait_for_event("close", timeout=int(timeout * 1000))
                    except Exception:
                        pass
            else:
                # 等待用户关闭浏览器或超时
                try:
                    await page.wait_for_event("close", timeout=int(timeout * 1000))
                except Exception:
                    logger.warning("[ProfileManager] 登录超时，强制保存当前状态")

            # 保存 cookie 信息
            cookies = await context.cookies()
            try:
                await context.storage_state(path=str(Path(profile_dir) / "storage_state.json"))
            except Exception as e:
                logger.debug(f"Failed to save storage state: {e}")

            await context.close()

        # 更新元数据
        self._metadata[domain] = {
            "url": url,
            "cookies_count": len(cookies),
            "created_at": self._metadata.get(domain, {}).get("created_at", time.time()),
            "updated_at": time.time(),
            "profile_dir": profile_dir,
        }
        self._save_metadata()

        logger.info(
            f"[ProfileManager] 登录态已保存: domain={domain}, cookies={len(cookies)}"
        )

        return {
            "success": len(cookies) > 0,
            "cookies_count": len(cookies),
            "domain": domain,
            "profile_dir": profile_dir,
        }

    async def fetch_with_profile(
        self,
        url: str,
        *,
        headless: bool = True,
        wait_for: str = "domcontentloaded",
        timeout: float = 30.0,
        inject_js: bool = True,
    ) -> Dict[str, Any]:
        """使用已有 profile 抓取页面（自动携带登录态）

        Args:
            url: 目标 URL
            headless: 是否无头模式
            wait_for: 页面加载等待策略
            timeout: 超时秒数
            inject_js: 是否注入 JS 片段

        Returns:
            Dict: 抓取结果（status_code, html, title, cookies）
        """
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError("Playwright not installed")

        domain = self._domain_key(url)
        if not self.has_profile(url):
            raise ValueError(f"No profile found for {domain}, run login_interactive first")

        profile_dir = self.get_profile_dir(url)

        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                profile_dir,
                headless=headless,
                args=["--disable-blink-features=AutomationControlled"],
            )

            # 注入 JS
            if inject_js:
                try:
                    from crawagent.js_snippets import get_all_snippets
                    await context.add_init_script(get_all_snippets())
                except Exception as e:
                    logger.debug(f"JS injection failed: {e}")

            page = context.pages[0] if context.pages else await context.new_page()

            response = await page.goto(url, wait_until=wait_for, timeout=int(timeout * 1000))
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass

            result = {
                "status_code": response.status if response else 0,
                "html": await page.content(),
                "title": await page.title(),
                "url": page.url,
                "cookies": await context.cookies(),
            }

            await context.close()

        return result

    def export_cookies_txt(self, url: str) -> str:
        """导出 Netscape cookies.txt（供 yt-dlp --cookies 使用）

        将 Playwright storage_state.json 中的 cookie 转换为
        Netscape 格式 cookies.txt，写入 profile 目录，
        之后可传给 MediaDownloader / yt-dlp 下载会员/登录类内容。

        Args:
            url: 域名 URL

        Returns:
            cookies.txt 的绝对路径
        """
        domain = self._domain_key(url)
        profile_dir = self.base_dir / domain
        storage_path = profile_dir / "storage_state.json"
        cookies_txt = profile_dir / "cookies.txt"

        if not storage_path.exists():
            raise FileNotFoundError(
                f"storage_state.json 不存在: {storage_path}，请先执行 login_interactive"
            )

        state = json.loads(storage_path.read_text(encoding="utf-8"))
        cookies = state.get("cookies", [])
        self._write_netscape(cookies, cookies_txt)
        return str(cookies_txt)

    @staticmethod
    def _write_netscape(cookies: List[Dict[str, Any]], path: Path) -> int:
        """把 cookie 列表写成 Netscape cookies.txt（供 yt-dlp 使用）

        Playwright cookie 与 cookies.txt 字段对应关系：
            domain → domain（带 . 前缀的写入 TRUE includeSubdomains）
            expires → Unix 时间戳（<=0 表示会话 cookie）
            secure / path / name / value 直传
        """
        lines = [
            "# Netscape HTTP Cookie File",
            "# Generated by CrawAgent ProfileManager — yt-dlp --cookies",
        ]
        seen = set()
        for c in cookies:
            domain_f = c.get("domain", "")
            cookie_path = c.get("path", "/")
            key = (domain_f, cookie_path, c.get("name", ""))
            if key in seen:
                continue
            seen.add(key)
            include_sub = "TRUE" if domain_f.startswith(".") else "FALSE"
            secure = "TRUE" if c.get("secure") else "FALSE"
            expires = int(c.get("expires", 0) or 0)
            if expires <= 0:
                expires = 0
            name = c.get("name", "")
            value = c.get("value", "").replace("\t", "%09").replace("\n", "%0A")
            lines.append(
                f"{domain_f}\t{include_sub}\t{cookie_path}\t{secure}\t{expires}\t{name}\t{value}"
            )
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return len(seen)

    async def grab_anonymous_cookies(
        self,
        url: str,
        *,
        wait_ms: int = 8000,
        scroll_rounds: int = 0,
    ) -> Dict[str, Any]:
        """自动获取匿名 cookie（无需登录）

        用 Playwright 无头浏览器访问目标站（抖音等风控站点会在 JS 渲染时
        种下 ttwid/msToken 等匿名 cookie），收集后保存 cookies.txt，
        供 yt-dlp 下载公开视频使用——公开视频无需账号登录。

        Args:
            url: 目标站点 URL
            wait_ms: 页面加载后等待时间（等风控 JS 种 cookie）
            scroll_rounds: 滚动轮数（触发懒加载，收集更多 cookie）

        Returns:
            {"success", "cookies_count", "domain", "cookies_file", "source"}
        """
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError("Playwright not installed")

        domain = self._domain_key(url)
        profile_dir = self.base_dir / domain
        profile_dir.mkdir(parents=True, exist_ok=True)

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-proxy-server",
                ],
            )
            try:
                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1280, "height": 900},
                    locale="zh-CN",
                )
                page = await context.new_page()
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                except Exception as e:
                    logger.debug(f"[ProfileManager] 匿名 cookie 导航异常: {e}")
                try:
                    await page.wait_for_load_state("networkidle", timeout=12000)
                except Exception:
                    pass
                await page.wait_for_timeout(wait_ms)
                for _ in range(scroll_rounds):
                    await page.mouse.wheel(0, 2500)
                    await page.wait_for_timeout(1200)
                cookies = await context.cookies()
            finally:
                await browser.close()

        # 保存 storage_state + cookies.txt
        state_path = profile_dir / "storage_state.json"
        try:
            state_path.write_text(
                json.dumps({"cookies": cookies, "origins": []}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.debug(f"Failed to save storage state: {e}")
        cookies_txt = profile_dir / "cookies.txt"
        n = self._write_netscape(cookies, cookies_txt)

        self._metadata[domain] = {
            "url": url,
            "cookies_count": len(cookies),
            "source": "anonymous",  # 匿名（无需登录） vs login（用户手动登录）
            "created_at": self._metadata.get(domain, {}).get("created_at", time.time()),
            "updated_at": time.time(),
            "profile_dir": str(profile_dir),
            "cookies_file": str(cookies_txt),
        }
        self._save_metadata()

        logger.info(
            f"[ProfileManager] 匿名 cookie 已保存: domain={domain}, cookies={len(cookies)}"
        )
        return {
            "success": len(cookies) > 0,
            "cookies_count": len(cookies),
            "domain": domain,
            "cookies_file": str(cookies_txt),
            "source": "anonymous",
        }

    def delete_profile(self, url: str) -> bool:
        """删除域名的 profile

        Args:
            url: 域名 URL

        Returns:
            是否删除成功
        """
        import shutil

        domain = self._domain_key(url)
        profile_dir = self.base_dir / domain

        if profile_dir.exists():
            shutil.rmtree(profile_dir, ignore_errors=True)

        if domain in self._metadata:
            del self._metadata[domain]
            self._save_metadata()

        logger.info(f"[ProfileManager] 已删除 profile: {domain}")
        return True

    def get_profile_info(self, url: str) -> Optional[Dict[str, Any]]:
        """获取域名的 profile 信息"""
        domain = self._domain_key(url)
        return self._metadata.get(domain)
