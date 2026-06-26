"""land8028.com RSS/XML 接口获取模块

通过 RSS/XML 接口高效获取网站所有节目信息，无需浏览器渲染。
支持的接口：
- /rss.xml - 主 RSS 订阅（包含最新节目）
- /rss/baidu.xml - 百度地图
- /rss/so.xml - 360地图
- /rss/sm.xml - 神马爬虫
- /rss/sogou.xml - 搜狗蜘蛛
"""
import requests
import xml.etree.ElementTree as ET
import json
import os
from typing import Any

from crawagent.config.settings import get_logger
logger = get_logger(__name__)


class Land8028RssAPI:
    """land8028.com RSS 接口客户端"""
    
    BASE_URL = "https://land8028.com"
    
    # RSS 接口列表
    RSS_ENDPOINTS = {
        "main": "/rss.xml",
        "baidu": "/rss/baidu.xml",
        "so": "/rss/so.xml",
        "sm": "/rss/sm.xml",
        "sogou": "/rss/sogou.xml",
    }
    
    def __init__(self, base_url: str = None):
        self.base_url = base_url or self.BASE_URL
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        })
    
    def _fetch_xml(self, endpoint: str) -> str | None:
        """获取 XML 数据"""
        url = f"{self.base_url}{endpoint}"
        try:
            resp = self.session.get(url, timeout=30)
            resp.encoding = 'utf-8'
            if resp.status_code == 200:
                return resp.text
        except Exception as e:
            logger.error(f"获取 {endpoint} 失败: {e}")
        return None
    
    def _parse_rss(self, xml_text: str) -> list[dict[str, Any]]:
        """解析 RSS XML 数据"""
        shows = []
        try:
            root = ET.fromstring(xml_text)
            for item in root.findall('.//item'):
                title = item.find('title')
                link = item.find('link')
                description = item.find('description')
                pub_date = item.find('pubDate')
                
                if title is not None and link is not None:
                    title_text = title.text.strip() if title.text else ""
                    link_text = link.text.strip() if link.text else ""
                    
                    # 解析标题，提取剧名和更新信息
                    show_name = title_text
                    episode_info = ""
                    if "更新至" in title_text:
                        parts = title_text.split("更新至", 1)
                        show_name = parts[0].strip()
                        episode_info = "更新至" + parts[1].strip()
                    
                    shows.append({
                        "title": title_text,
                        "show_name": show_name,
                        "episode_info": episode_info,
                        "link": link_text,
                        "description": description.text.strip() if description is not None and description.text else "",
                        "pub_date": pub_date.text.strip() if pub_date is not None and pub_date.text else "",
                    })
        except Exception as e:
            logger.error(f"解析 XML 失败: {e}")
        return shows
    
    def get_all_shows(self, endpoint: str = "main") -> list[dict[str, Any]]:
        """获取所有节目列表
        
        Args:
            endpoint: RSS 接口名称 (main/baidu/so/sm/sogou)
            
        Returns:
            节目列表
        """
        if endpoint not in self.RSS_ENDPOINTS:
            logger.warning(f"未知的接口: {endpoint}")
            return []
        
        xml_text = self._fetch_xml(self.RSS_ENDPOINTS[endpoint])
        if not xml_text:
            return []
        
        return self._parse_rss(xml_text)
    
    def get_show_detail(self, show_url: str) -> dict[str, Any]:
        """获取单个节目的详细信息（通过节目页面）
        
        Args:
            show_url: 节目页面 URL，如 https://land8028.com/weihu/1381674.html
            
        Returns:
            节目详细信息，包含剧集列表
        """
        result = {
            "show_name": "",
            "show_url": show_url,
            "episodes": [],
        }
        
        try:
            resp = self.session.get(show_url, timeout=30)
            resp.encoding = 'utf-8'
            html = resp.text
            
            # 提取剧名
            import re
            import html as html_module
            title_match = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
            if title_match:
                title_text = re.sub(r'<[^>]+>', '', title_match.group(1)).strip()
                result["show_name"] = html_module.unescape(title_text)
            
            # 提取剧集列表（从 con_playlist_1）
            playlist_match = re.search(
                r'id="con_playlist_1"(.*?)<\/ul>',
                html, re.DOTALL | re.IGNORECASE
            )
            if playlist_match:
                playlist_html = playlist_match.group(1)
                # 提取所有 a 标签
                links = re.findall(
                    r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                    playlist_html, re.DOTALL
                )
                for href, title in links:
                    title_clean = re.sub(r'<[^>]+>', '', title).strip()
                    title_clean = html_module.unescape(title_clean)
                    if href and title_clean:
                        # 构建完整 URL
                        if not href.startswith("http"):
                            from urllib.parse import urljoin
                            href = urljoin(show_url, href)
                        result["episodes"].append({
                            "title": title_clean,
                            "url": href,
                        })
            
            # 如果没有提取到，尝试其他选择器
            if not result["episodes"]:
                # 尝试匹配所有包含"第x集"的链接
                all_links = re.findall(
                    r'<a[^>]+href="([^"]+)"[^>]*>(第[\d一二三四五六七八九十]+集.*?)</a>',
                    html, re.DOTALL
                )
                for href, title in all_links:
                    title_clean = re.sub(r'<[^>]+>', '', title).strip()
                    title_clean = html_module.unescape(title_clean)
                    if href and title_clean:
                        if not href.startswith("http"):
                            from urllib.parse import urljoin
                            href = urljoin(show_url, href)
                        result["episodes"].append({
                            "title": title_clean,
                            "url": href,
                        })
        
        except Exception as e:
            logger.error(f"获取节目详情失败: {e}")
        
        return result
    
    def search_shows(self, keyword: str, endpoint: str = "main") -> list[dict[str, Any]]:
        """搜索节目
        
        Args:
            keyword: 搜索关键词
            endpoint: RSS 接口名称
            
        Returns:
            匹配的节目列表
        """
        all_shows = self.get_all_shows(endpoint)
        keyword_lower = keyword.lower()
        
        results = []
        for show in all_shows:
            if (keyword_lower in show.get("show_name", "").lower() or
                keyword_lower in show.get("title", "").lower() or
                keyword_lower in show.get("description", "").lower()):
                results.append(show)
        
        return results
    
    def save_to_json(self, shows: list[dict], filename: str = None, 
                     output_dir: str = "output") -> str:
        """保存节目列表到 JSON 文件
        
        Args:
            shows: 节目列表
            filename: 文件名（可选）
            output_dir: 输出目录
            
        Returns:
            保存的文件路径
        """
        os.makedirs(output_dir, exist_ok=True)
        
        if not filename:
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"land8028_shows_{timestamp}.json"
        
        filepath = os.path.join(output_dir, filename)
        
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(shows, f, ensure_ascii=False, indent=2)
        
        logger.info(f"已保存到: {filepath}")
        return filepath


# 便捷函数
def get_land8028_shows(endpoint: str = "main") -> list[dict[str, Any]]:
    """获取 land8028.com 的所有节目列表"""
    api = Land8028RssAPI()
    return api.get_all_shows(endpoint)


def search_land8028_shows(keyword: str, endpoint: str = "main") -> list[dict[str, Any]]:
    """搜索 land8028.com 的节目"""
    api = Land8028RssAPI()
    return api.search_shows(keyword, endpoint)


def get_land8028_show_detail(show_url: str) -> dict[str, Any]:
    """获取 land8028.com 单个节目的详细信息"""
    api = Land8028RssAPI()
    return api.get_show_detail(show_url)
