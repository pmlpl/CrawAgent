"""
凤凰影院视频解析器
网站: https://land8028.com/

接口分析:
- 视频详情页: /weihu/{video_id}.html - 显示视频信息和集数列表
- 播放页: /huishou/{video_id}-{some_id}-{episode}.html - 包含 player_aaaa JavaScript 变量，含有视频 URL
- 视频 URL: 从 player_aaaa.url 提取，格式是 m3u8 (HLS)
"""

import requests
import re
import json
from typing import Optional, List, Dict, Any


class FengHuangVideoParser:
    """凤凰影院视频解析器"""
    
    BASE_URL = "https://land8028.com"
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://land8028.com/',
    }
    
    def __init__(self, use_db: bool = True, auto_update: bool = True):
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
        self.session.verify = False
        # 禁用系统代理，避免代理连接错误
        self.session.trust_env = False
        self.session.proxies = {"http": None, "https": None}
        self.use_db = use_db
        self._db = None
        self._last_update_check = None
        self._auto_update = auto_update
        
        # 增量更新：启动时检查新视频
        if self._auto_update:
            import threading
            threading.Thread(target=self._incremental_update, daemon=True).start()
    
    def _incremental_update(self, check_pages: int = 3):
        """增量更新：检查每个分类的最新几页，获取新视频
        
        Args:
            check_pages: 每个分类检查的最新页数
        """
        import html as html_mod
        import time
        from datetime import datetime
        
        # 避免频繁更新（间隔至少5分钟）
        now = datetime.now().timestamp()
        if self._last_update_check and (now - self._last_update_check) < 300:
            return
        
        self._last_update_check = now
        
        # 热门分类列表
        hot_categories = {
            '3': '综艺',      # 综艺
            '14': '国产剧',   # 国产剧
            '15': '港剧',     # 港剧
            '17': '韩剧',     # 韩剧
            '7': '动作片',    # 动作片
        }
        
        try:
            for cat_id, category in hot_categories.items():
                # 获取分类的总页数
                first_page_url = f"{self.BASE_URL}/show/{cat_id}-----------.html"
                resp = self._request(first_page_url, timeout=10)
                if not resp:
                    continue
                
                resp.encoding = 'utf-8'
                text = html_mod.unescape(resp.text)
                
                # 找尾页
                import re
                last_match = re.search(rf'href=["\']([^"\']*show/{cat_id}--------(\d+)---[^"\']*)["\'][^>]*>(?:尾页|最后一页)', text)
                if not last_match:
                    continue
                
                total_pages = int(last_match.group(2))
                
                # 检查最新几页
                for page_num in range(max(1, total_pages - check_pages + 1), total_pages + 1):
                    page_url = f"{self.BASE_URL}/show/{cat_id}--------{page_num}---.html"
                    resp = self._request(page_url, timeout=10)
                    if not resp:
                        continue
                    
                    resp.encoding = 'utf-8'
                    text = html_mod.unescape(resp.text)
                    
                    # 提取视频
                    videos = self._extract_video_list(text)
                    for v in videos:
                        v['category'] = category
                        self._save_search_result_to_db(v)
                    
                    time.sleep(0.2)
        except Exception:
            pass  # 增量更新失败不影响主功能
    
    @property
    def db(self):
        """懒加载数据库连接"""
        if self._db is None and self.use_db:
            try:
                from crawagent.tools.database import get_default_db
                self._db = get_default_db()
            except Exception:
                self._db = None
        return self._db
    
    def _request(self, url: str, timeout: int = 15) -> Optional[requests.Response]:
        """发起请求，自动重试
        
        Args:
            url: URL
            timeout: 超时时间
            
        Returns:
            Response 对象，失败返回 None
        """
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        
        for i in range(3):
            try:
                resp = self.session.get(url, timeout=timeout)
                if resp.status_code == 200:
                    return resp
            except Exception:
                if i == 2:
                    return None
        return None
    
    def _get_cache_key(self, video_id: str) -> str:
        """生成视频信息的缓存键（URL）"""
        return f"{self.BASE_URL}/weihu/{video_id}.html"
    
    def _save_video_info_to_db(self, info: dict):
        """保存视频信息到数据库
        
        Args:
            info: 视频信息字典
        """
        if not self.db or not info:
            return
        
        try:
            url = self._get_cache_key(info['video_id'])
            extra_data = {
                'cover': info.get('cover', ''),
                'status': info.get('status', ''),
                'type': info.get('type', ''),
                'actors': info.get('actors', []),
                'director': info.get('director', ''),
                'year': info.get('year', ''),
                'intro': info.get('intro', ''),
                'is_variety': info.get('is_variety', False),
                'episodes_count': len(info.get('episodes', [])),
                'episodes': info.get('episodes', []),
            }
            self.db.save_crawl(
                url=url,
                title=info.get('title', ''),
                content=info.get('intro', ''),
                source='fenghuang',
                category=info.get('type', ''),
                extra_data=extra_data
            )
        except Exception:
            pass
    
    def _get_video_info_from_db(self, video_id: str) -> Optional[dict]:
        """从数据库获取视频信息（缓存）
        
        Args:
            video_id: 视频ID
            
        Returns:
            视频信息字典，未找到返回 None
        """
        if not self.db:
            return None
        
        try:
            url = self._get_cache_key(video_id)
            records = self.db.query(url=url, limit=1)
            if records:
                record = records[0]
                extra = record.extra_data or {}
                return {
                    'video_id': video_id,
                    'title': record.title,
                    'cover': extra.get('cover', ''),
                    'status': extra.get('status', ''),
                    'type': extra.get('type', ''),
                    'actors': extra.get('actors', []),
                    'director': extra.get('director', ''),
                    'year': extra.get('year', ''),
                    'intro': extra.get('intro', record.content),
                    'is_variety': extra.get('is_variety', False),
                    'episodes': extra.get('episodes', []),
                    'sid': extra.get('sid', ''),
                    'cid': extra.get('cid', ''),
                    '_from_cache': True
                }
        except Exception:
            pass
        return None
    
    def get_video_info(self, video_id: str, use_cache: bool = True) -> dict:
        """获取视频信息（标题、集数列表、封面、简介等）
        
        Args:
            video_id: 视频 ID，如 '1382277'
            use_cache: 是否使用数据库缓存
            
        Returns:
            包含视频信息的字典
        """
        import html as html_mod
        
        # 先尝试从数据库缓存获取
        if use_cache:
            cached = self._get_video_info_from_db(video_id)
            if cached and cached.get('episodes'):
                return cached
        
        url = f"{self.BASE_URL}/weihu/{video_id}.html"
        resp = self._request(url)
        if not resp:
            # 如果请求失败但有缓存，返回缓存
            if use_cache:
                cached = self._get_video_info_from_db(video_id)
                if cached:
                    return cached
            return {
                'video_id': video_id,
                'title': '',
                'cover': '',
                'status': '',
                'type': '',
                'actors': [],
                'director': '',
                'year': '',
                'intro': '',
                'is_variety': False,
                'sid': '',
                'cid': '',
                'episodes': []
            }
        
        resp.encoding = 'utf-8'
        
        # 先解码整个页面的HTML实体，方便后续正则匹配
        page_text = html_mod.unescape(resp.text)
        
        # 提取标题
        title_match = re.search(r'<h1[^>]*class="[^"]*text-overflow[^"]*"[^>]*>([^<]+)</h1>', page_text)
        if not title_match:
            title_match = re.search(r'<title>([^<]+)</title>', page_text)
        title = ''
        if title_match:
            title = title_match.group(1).strip()
            # 清理标题前缀（去掉《》等）
            title = title.strip('《》 ')
        
        # 提取封面图
        cover = ''
        cover_match = re.search(r'<div[^>]+class="[^"]*video-pic[^"]*"[^>]*>\s*<img[^>]+src=["\']([^"\']+)["\']', page_text)
        if cover_match:
            cover = cover_match.group(1)
            if cover and not cover.startswith('http'):
                cover = self.BASE_URL + cover
        
        # 提取状态（更新至XX集/完结等）
        status = ''
        status_match = re.search(r'状态[：:]\s*</span>\s*([^<]+)', page_text)
        if status_match:
            status = status_match.group(1).strip()
        
        # 提取类型
        video_type = ''
        type_match = re.search(r'类型[：:]\s*</span>\s*<a[^>]*>([^<]+)</a>', page_text)
        if type_match:
            video_type = type_match.group(1).strip()
        
        # 提取主演
        actors = []
        actor_pattern = r'主演[：:]\s*</span>\s*([\s\S]*?)</div>'
        actor_match = re.search(actor_pattern, page_text)
        if actor_match:
            actor_html = actor_match.group(1)
            actor_names = re.findall(r'<a[^>]*>([^<]+)</a>', actor_html)
            actors = [a.strip() for a in actor_names if a.strip()]
        
        # 提取导演
        director = ''
        director_match = re.search(r'导演[：:]\s*</span>\s*<a[^>]*>([^<]+)</a>', page_text)
        if director_match:
            director = director_match.group(1).strip()
        
        # 提取年代
        year = ''
        year_match = re.search(r'年代[：:]\s*</span>\s*(\d+)', page_text)
        if year_match:
            year = year_match.group(1)
        
        # 提取简介
        intro = ''
        intro_match = re.search(r'简介[：:]\s*</span>\s*([^<]+)', page_text)
        if intro_match:
            intro = intro_match.group(1).strip()
        
        # 提取 cms 配置获取 sid, cid
        cms_match = re.search(r'cms\s*=\s*\{([^}]+)\}', page_text)
        cms_config = {}
        if cms_match:
            for item in cms_match.group(1).split(','):
                if ':' in item:
                    key, val = item.split(':', 1)
                    cms_config[key.strip()] = val.strip().strip('"').strip("'")
        
        # 提取集数列表
        episodes = []
        episode_links = re.findall(r'href=["\']([^"\']*/huishou/(\d+)-(\d+)-(\d+)\.html[^"\']*)["\']', page_text)
        for full_url, vid, some_id, ep in episode_links:
            episodes.append({
                'url': full_url if full_url.startswith('http') else self.BASE_URL + full_url,
                'video_id': vid,
                'some_id': some_id,
                'episode': int(ep) if ep.isdigit() else ep
            })
        
        # 按集数排序
        episodes.sort(key=lambda x: x['episode'] if isinstance(x['episode'], int) else 0)
        
        # 判断是否为综艺（类型包含"综艺"）
        is_variety = '综艺' in video_type
        
        result = {
            'video_id': video_id,
            'title': title,
            'cover': cover,
            'status': status,
            'type': video_type,
            'actors': actors,
            'director': director,
            'year': year,
            'intro': intro,
            'is_variety': is_variety,
            'sid': cms_config.get('sid', ''),
            'cid': cms_config.get('cid', ''),
            'episodes': episodes
        }
        
        # 保存到数据库缓存
        if title and episodes:
            self._save_video_info_to_db(result)
        
        return result
    
    def get_play_url(self, video_id: str, some_id: str, episode: str) -> dict:
        """获取播放页面的视频 URL
        
        Args:
            video_id: 视频 ID
            some_id: 一些 ID
            episode: 集数
            
        Returns:
            包含播放信息的字典
        """
        url = f"{self.BASE_URL}/huishou/{video_id}-{some_id}-{episode}.html"
        resp = self.session.get(url, timeout=15)
        resp.encoding = 'utf-8'
        
        # 提取 player_aaaa 配置
        player_match = re.search(r'var player_aaaa\s*=\s*(\{[^}]+\})', resp.text)
        if not player_match:
            return {'success': False, 'error': '未找到播放器配置'}
        
        player_config_str = player_match.group(1)
        
        # 解析 JSON
        try:
            player_config_str = player_config_str.replace('\\/', '/')
            player_config = json.loads(player_config_str)
        except json.JSONDecodeError as e:
            return {'success': False, 'error': f'JSON 解析失败: {e}'}
        
        return {
            'success': True,
            'url': player_config.get('url', ''),
            'from': player_config.get('from', ''),
            'link': player_config.get('link', ''),
            'link_next': player_config.get('link_next', ''),
            'link_pre': player_config.get('link_pre', ''),
            'video_id': player_config.get('id', ''),
            'sid': player_config.get('sid', ''),
            'nid': player_config.get('nid', ''),
        }
    
    def get_episode_url(self, episode_url: str) -> dict:
        """从播放页面 URL 获取视频 URL
        
        Args:
            episode_url: 完整的播放页面 URL
            
        Returns:
            包含播放信息的字典
        """
        match = re.search(r'/huishou/(\d+)-(\d+)-(\d+)\.html', episode_url)
        if not match:
            return {'success': False, 'error': 'URL 格式不正确'}
        
        video_id, some_id, episode = match.groups()
        return self.get_play_url(video_id, some_id, episode)
    
    def _clean_keyword(self, keyword: str) -> str:
        """清理搜索关键词，去除语气词、标点、书名号等
        
        Args:
            keyword: 原始关键词
            
        Returns:
            清理后的关键词
        """
        if not keyword:
            return keyword
        
        # 去掉前后空格
        keyword = keyword.strip()
        
        # 去掉书名号、引号等
        keyword = keyword.replace('《', '').replace('》', '')
        keyword = keyword.replace('【', '').replace('】', '')
        keyword = keyword.replace('「', '').replace('」', '')
        keyword = keyword.replace('“', '').replace('”', '')
        keyword = keyword.replace('"', '').replace("'", '')
        
        # 去掉前缀（我想看、搜索、找一下、播放、看、帮我找等）
        prefix_patterns = [
            r'^我想看',
            r'^我要看',
            r'^我想找',
            r'^帮我找',
            r'^帮我放',
            r'^搜索',
            r'^查一下',
            r'^找一下',
            r'^找个',
            r'^看个',
            r'^看一下',
            r'^播放',
            r'^放一下',
            r'^有没有',
            r'^有什么',
            r'^给我',
            r'^推荐',
        ]
        import re
        for pattern in prefix_patterns:
            keyword = re.sub(pattern, '', keyword)
        
        # 去掉后缀（节目、视频、电影、电视剧、综艺、动画片等）
        suffix_patterns = [
            r'节目$',
            r'视频$',
            r'电影$',
            r'电视剧$',
            r'综艺$',
            r'动画片$',
            r'动漫$',
            r'剧集$',
            r'全集$',
            r'完整版$',
            r'高清$',
            r'在线观看$',
            r'免费观看$',
            r'好看的$',
        ]
        for pattern in suffix_patterns:
            keyword = re.sub(pattern, '', keyword)
        
        # 再次去掉前后空格和标点
        keyword = keyword.strip()
        keyword = keyword.strip('，。！？、；：, . ! ?')
        
        return keyword
    
    def search(self, keyword: str, use_browser: bool = True, page: int = 1, page_size: int = 20, save_to_db: bool = True) -> dict:
        """搜索视频
        
        搜索策略：
        1. 先从数据库搜索（快速返回已有结果）
        2. 同时去网站爬取最新结果（后台更新数据库）
        3. 爬取到的新视频自动存入数据库
        4. 合并数据库 + 网站结果返回
        
        Args:
            keyword: 搜索关键词
            use_browser: 是否使用浏览器模式（requests 失败时自动降级）
            page: 页码（从1开始）
            page_size: 每页数量
            save_to_db: 是否将爬取结果存入数据库
            
        Returns:
            包含搜索结果的字典：{results: [...], total: int, page: int, page_size: int}
        """
        import html as html_mod
        
        # 清理关键词
        cleaned_keyword = self._clean_keyword(keyword)
        if not cleaned_keyword:
            return {
                'results': [],
                'total': 0,
                'page': page,
                'page_size': page_size,
                'has_more': False,
                'original_keyword': keyword,
                'cleaned_keyword': cleaned_keyword
            }
        
        all_results = []
        seen_ids = set()
        
        # 方案0: 先从数据库搜索
        db_results = self._search_from_db(cleaned_keyword)
        for r in db_results:
            if r['video_id'] not in seen_ids:
                seen_ids.add(r['video_id'])
                all_results.append(r)
        
        # 方案1: 去网站搜索（无论数据库有没有，都去网站找最新的）
        web_results = []
        try:
            # 尝试网站搜索URL
            search_url = f"{self.BASE_URL}/index.php?s=/home/search/wd/{cleaned_keyword}.html"
            if page > 1:
                search_url += f"&page={page}"
            resp = self._request(search_url, timeout=15)
            if resp and resp.status_code == 200:
                resp.encoding = 'utf-8'
                page_text = html_mod.unescape(resp.text)
                if '404' not in page_text[:500] and 'baofahu' not in page_text[:500]:
                    results = self._extract_video_list(page_text)
                    for r in results:
                        score = self._calc_match_score(cleaned_keyword, r['title'])
                        if score >= 80:
                            r['_score'] = score
                            web_results.append(r)
                    web_results.sort(key=lambda x: x['_score'], reverse=True)
                    for r in web_results:
                        r.pop('_score', None)
        except Exception:
            pass
        
        # 方案2: 如果网站搜索不行，从首页+分类页提取视频做本地模糊匹配
        if not web_results:
            try:
                web_results = self._search_from_homepage(cleaned_keyword)
            except Exception:
                pass
        
        # 方案3: Playwright 浏览器模式
        if not web_results and use_browser:
            try:
                web_results = self._search_with_browser(cleaned_keyword)
            except Exception:
                pass
        
        # 合并结果（数据库 + 网站），新的结果添加到后面
        new_count = 0
        for r in web_results:
            if r['video_id'] not in seen_ids:
                seen_ids.add(r['video_id'])
                all_results.append(r)
                new_count += 1
                # 存入数据库
                if save_to_db and self.db:
                    self._save_search_result_to_db(r)
        
        # 补充缺失的封面图
        for r in all_results:
            if not r.get('cover'):
                try:
                    # 尝试从数据库详情获取封面
                    detail = self._get_video_info_from_db(r['video_id'])
                    if detail and detail.get('cover'):
                        r['cover'] = detail['cover']
                except Exception:
                    pass
        
        # 按匹配度重新排序
        scored_all = []
        for r in all_results:
            score = self._calc_match_score(cleaned_keyword, r['title'])
            r['_score'] = score
            scored_all.append(r)
        scored_all.sort(key=lambda x: x['_score'], reverse=True)
        for r in scored_all:
            r.pop('_score', None)
        all_results = scored_all
        
        # 分页处理
        total = len(all_results)
        start = (page - 1) * page_size
        end = start + page_size
        paged_results = all_results[start:end]
        
        return {
            'results': paged_results,
            'total': total,
            'page': page,
            'page_size': page_size,
            'has_more': end < total,
            'new_found': new_count  # 本次新发现的视频数量
        }
    
    def _search_from_db(self, keyword: str) -> list:
        """从数据库搜索视频
        
        Args:
            keyword: 搜索关键词
            
        Returns:
            匹配的视频列表
        """
        if not self.db:
            return []
        
        try:
            # 从数据库搜索（标题和内容匹配）
            records = self.db.query(keyword=keyword, source='fenghuang', limit=200)
            
            results = []
            for record in records:
                extra = record.extra_data or {}
                title = record.title
                score = self._calc_match_score(keyword, title)
                if score >= 80:
                    results.append({
                        'url': record.url,
                        'video_id': extra.get('video_id', '') or record.url.split('/')[-1].replace('.html', ''),
                        'title': title,
                        'cover': extra.get('cover', ''),
                        'status': extra.get('status', ''),
                        'type': extra.get('type', ''),
                        'intro': record.content or extra.get('intro', ''),
                        '_score': score,
                        '_from_db': True
                    })
            
            results.sort(key=lambda x: x['_score'], reverse=True)
            for r in results:
                r.pop('_score', None)
                r.pop('_from_db', None)
            
            return results
        except Exception:
            return []
    
    def _save_search_result_to_db(self, result: dict):
        """将搜索结果保存到数据库（轻量保存，详细信息在get_video_info时再补全）
        
        Args:
            result: 搜索结果字典
        """
        if not self.db or not result:
            return
        
        try:
            url = result.get('url', '')
            if not url:
                return
            
            # 获取现有记录，保留已有的封面等信息
            existing_records = self.db.query(url=url, limit=1)
            existing_cover = ''
            if existing_records:
                existing_extra = existing_records[0].extra_data or {}
                existing_cover = existing_extra.get('cover', '')
            
            # 新封面为空时，保留原有封面
            new_cover = result.get('cover', '') or existing_cover
            
            extra_data = {
                'video_id': result.get('video_id', ''),
                'cover': new_cover,
                'type': result.get('type', ''),
                'status': result.get('status', ''),
            }
            
            self.db.save_crawl(
                url=url,
                title=result.get('title', ''),
                content=result.get('intro', ''),
                source='fenghuang',
                category=result.get('type', ''),
                extra_data=extra_data
            )
        except Exception:
            pass
    
    def _extract_video_list(self, page_text: str) -> list:
        """从页面HTML中提取视频列表（标题、video_id、封面图）
        
        Args:
            page_text: 已解码HTML实体的页面文本
            
        Returns:
            视频列表，每项包含 title, video_id, url, cover
        """
        results = []
        seen = set()
        
        # 模式1: 匹配带封面图的视频卡片，用a标签的title属性+img src
        # 结构: <a href="weihu/xxx.html" title="视频标题"><img src="xxx.jpg"></a>
        pattern1 = r'<a[^>]+href=["\']([^"\']*weihu/(\d+)\.html[^"\']*)["\'][^>]*title=["\']([^"\']+)["\'][^>]*>[\s\S]*?<img[^>]+src=["\']([^"\']+)["\']'
        for url, vid, title, cover in re.findall(pattern1, page_text):
            if vid in seen:
                continue
            seen.add(vid)
            title = title.strip()
            if not title or len(title) < 2 or title.isdigit():
                continue
            if cover and not cover.startswith('http'):
                cover = self.BASE_URL + cover
            full_url = url if url.startswith('http') else self.BASE_URL + url
            results.append({
                'url': full_url,
                'video_id': vid,
                'title': title,
                'cover': cover
            })
        
        # 模式2: 匹配 img alt 属性作为标题 + 附近的 weihu 链接
        if len(results) < 20:
            pattern2 = r'<a[^>]+href=["\']([^"\']*weihu/(\d+)\.html[^"\']*)["\'][^>]*>[\s\S]*?<img[^>]+src=["\']([^"\']+)["\'][^>]*alt=["\']([^"\']+)["\']'
            for url, vid, cover, title in re.findall(pattern2, page_text):
                if vid in seen:
                    continue
                seen.add(vid)
                title = title.strip()
                if not title or len(title) < 2 or title.isdigit():
                    continue
                if cover and not cover.startswith('http'):
                    cover = self.BASE_URL + cover
                full_url = url if url.startswith('http') else self.BASE_URL + url
                results.append({
                    'url': full_url,
                    'video_id': vid,
                    'title': title,
                    'cover': cover
                })
        
        # 模式3: 热门搜索列表（只有标题，没有封面）
        if len(results) < 10:
            pattern3 = r'<a[^>]+href=["\']([^"\']*weihu/(\d+)\.html[^"\']*)["\'][^>]*>\s*<i>\d+</i>\s*([^<]+)\s*</a>'
            for url, vid, title in re.findall(pattern3, page_text):
                if vid in seen:
                    continue
                seen.add(vid)
                title = title.strip()
                if not title or len(title) < 2 or title.isdigit():
                    continue
                full_url = url if url.startswith('http') else self.BASE_URL + url
                results.append({
                    'url': full_url,
                    'video_id': vid,
                    'title': title,
                    'cover': ''
                })
        
        # 模式4: 兜底，简单链接+文本
        if len(results) < 5:
            pattern4 = r'href=["\']([^"\']*weihu/(\d+)[^"\']*\.html[^"\']*)["\'][^>]*>([^<]+)<'
            for url, vid, title in re.findall(pattern4, page_text):
                if vid in seen:
                    continue
                seen.add(vid)
                title = title.strip()
                if not title or len(title) < 2 or title.isdigit():
                    continue
                full_url = url if url.startswith('http') else self.BASE_URL + url
                results.append({
                    'url': full_url,
                    'video_id': vid,
                    'title': title,
                    'cover': ''
                })
        
        return results
    
    def _calc_match_score(self, keyword: str, title: str) -> int:
        """计算关键词与标题的匹配度分数
        
        Args:
            keyword: 搜索关键词
            title: 视频标题
            
        Returns:
            匹配度分数（越高越匹配）
        """
        keyword_lower = keyword.lower()
        title_lower = title.lower()
        
        score = 0
        
        # 完全匹配（最高分）
        if keyword_lower == title_lower:
            return 1000
        
        # 标题包含完整关键词（高分）
        if keyword_lower in title_lower:
            score += 600
            # 关键词在标题中的位置越靠前分数越高
            idx = title_lower.find(keyword_lower)
            score += max(0, 100 - idx * 2)
            return score  # 包含完整关键词直接返回，不需要再计算其他
        
        # 关键词的连续子串匹配（按长度加权）
        # 比如搜索"奔跑吧兄弟"，标题有"奔跑吧" → 3字连续匹配
        max_consecutive = 0
        kw_len = len(keyword_lower)
        for start in range(kw_len):
            for end in range(start + 2, kw_len + 1):
                sub = keyword_lower[start:end]
                if sub in title_lower:
                    sub_len = len(sub)
                    if sub_len > max_consecutive:
                        max_consecutive = sub_len
        
        if max_consecutive >= 2:
            score += max_consecutive * max_consecutive * 20  # 平方加权：2字=80, 3字=180, 4字=320
        
        # 标题的更多字匹配关键词（额外加分）
        # 比如搜索"奔跑吧兄弟"，标题有"奔跑吧"+"兄弟" → 额外加分
        if max_consecutive >= 2 and len(keyword_lower) > 2:
            extra_chars = 0
            # 去掉已经匹配的最长连续子串，看看还有没有其他字匹配
            title_remaining = title_lower
            # 找最长连续子串并移除
            for start in range(kw_len):
                for end in range(start + max_consecutive, kw_len + 1):
                    sub = keyword_lower[start:end]
                    if sub in title_remaining and len(sub) == max_consecutive:
                        title_remaining = title_remaining.replace(sub, '', 1)
                        break
                else:
                    continue
                break
            # 看看剩余的标题中还有多少关键词的字
            for c in keyword_lower:
                if c in title_remaining:
                    extra_chars += 1
                    title_remaining = title_remaining.replace(c, '', 1)
            score += extra_chars * 10
        
        return score
    
    def _search_from_homepage(self, keyword: str) -> list:
        """从首页和分类页提取视频列表，做本地模糊匹配搜索
        
        Args:
            keyword: 搜索关键词
            
        Returns:
            匹配的视频列表
        """
        import html as html_mod
        
        all_videos = []
        seen = set()
        
        # 要爬取的页面列表
        urls_to_crawl = [
            self.BASE_URL,  # 首页
        ]
        
        # 添加几个分类页（综艺、电视剧、电影等），获取更多视频
        category_urls = [
            f"{self.BASE_URL}/show/21---%E5%A4%A7%E9%99%86%E7%BB%BC%E8%89%BA--------.html",  # 大陆综艺
            f"{self.BASE_URL}/show/22---%E6%B8%AF%E5%8F%B0%E7%BB%BC%E8%89%BA--------.html",  # 港台综艺
            f"{self.BASE_URL}/show/1---%E5%A4%A7%E9%99%86%E7%94%B5%E8%A7%86%E5%89%A7--------.html",  # 大陆电视剧
            f"{self.BASE_URL}/show/5---%E5%A4%A7%E9%99%86%E7%94%B5%E5%BD%B1--------.html",  # 大陆电影
        ]
        urls_to_crawl.extend(category_urls)
        
        for url in urls_to_crawl:
            try:
                resp = self._request(url, timeout=15)
                if not resp:
                    continue
                resp.encoding = 'utf-8'
                page_text = html_mod.unescape(resp.text)
                
                # 提取视频列表
                videos = self._extract_video_list(page_text)
                for v in videos:
                    if v['video_id'] not in seen:
                        seen.add(v['video_id'])
                        all_videos.append(v)
            except Exception:
                continue
            
            # 限制数量，避免爬取太多
            if len(all_videos) >= 200:
                break
        
        # 计算匹配度并排序
        scored_results = []
        for v in all_videos:
            score = self._calc_match_score(keyword, v['title'])
            if score >= 80:  # 最低匹配度阈值（至少2字连续匹配）
                v['_score'] = score
                scored_results.append(v)
        
        scored_results.sort(key=lambda x: x['_score'], reverse=True)
        for r in scored_results:
            r.pop('_score', None)
        
        return scored_results[:50]
    
    def _search_with_browser(self, keyword: str) -> list:
        """使用 Playwright 浏览器模式从首页提取视频并搜索（绕过反爬）
        
        Args:
            keyword: 搜索关键词
            
        Returns:
            搜索结果列表
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return []
        
        results = []
        
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(user_agent=self.HEADERS['User-Agent'])
                
                try:
                    page.goto(self.BASE_URL, wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    pass
                
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                page.wait_for_timeout(2000)
                
                # 提取所有 weihu 链接
                links = page.query_selector_all("a[href*='weihu/']")
                seen = set()
                keyword_lower = keyword.lower()
                
                for link in links:
                    href = link.get_attribute("href") or ""
                    text = link.inner_text().strip()
                    
                    if not href or not text:
                        continue
                    
                    vid_match = re.search(r'weihu/(\d+)', href)
                    if not vid_match:
                        continue
                    
                    vid = vid_match.group(1)
                    if vid in seen:
                        continue
                    
                    # 去掉开头的数字序号
                    text_clean = re.sub(r'^\d+', '', text).strip()
                    
                    # 过滤掉太短或太长的标题（可能是导航链接）
                    if len(text_clean) < 2 or len(text_clean) > 50:
                        continue
                    
                    # 模糊匹配
                    text_lower = text_clean.lower()
                    if keyword_lower in text_lower:
                        seen.add(vid)
                        full_url = href if href.startswith('http') else self.BASE_URL + href
                        results.append({
                            'url': full_url,
                            'video_id': vid,
                            'title': text_clean
                        })
                
                # 如果直接匹配不到，试试部分匹配
                if not results and len(keyword) >= 2:
                    seen2 = set()
                    for link in links:
                        href = link.get_attribute("href") or ""
                        text = link.inner_text().strip()
                        text_clean = re.sub(r'^\d+', '', text).strip()
                        
                        if not href or not text_clean or len(text_clean) < 2 or len(text_clean) > 50:
                            continue
                        
                        vid_match = re.search(r'weihu/(\d+)', href)
                        if not vid_match:
                            continue
                        
                        vid = vid_match.group(1)
                        if vid in seen2:
                            continue
                        
                        matched_chars = sum(1 for c in keyword if c in text_clean)
                        if matched_chars >= len(keyword) * 0.5:
                            seen2.add(vid)
                            full_url = href if href.startswith('http') else self.BASE_URL + href
                            results.append({
                                'url': full_url,
                                'video_id': vid,
                                'title': text_clean,
                                '_score': matched_chars
                            })
                    
                    results.sort(key=lambda x: x.get('_score', 0), reverse=True)
                    for r in results:
                        r.pop('_score', None)
                
                browser.close()
        except Exception:
            pass
        
        return results[:10]


def parse_video(video_url_or_id: str) -> dict:
    """解析视频 URL 或 ID，返回视频信息和播放地址
    
    Args:
        video_url_or_id: 视频 URL (如 https://land8028.com/weihu/1382277.html) 
                        或视频 ID (如 1382277)
                        或播放页面 URL (如 https://land8028.com/huishou/1382277-1-6.html)
        
    Returns:
        包含视频信息和播放地址的字典
    """
    parser = FengHuangVideoParser()
    
    # 判断输入类型
    if '/' in video_url_or_id:
        # 可能是 URL
        if '/huishou/' in video_url_or_id:
            # 播放页面 URL
            return parser.get_episode_url(video_url_or_id)
        match = re.search(r'/weihu/(\d+)\.html', video_url_or_id)
        if match:
            video_id = match.group(1)
        else:
            return {'success': False, 'error': 'URL 格式不正确'}
    else:
        # 视频 ID
        video_id = video_url_or_id
    
    # 获取视频信息
    info = parser.get_video_info(video_id)
    
    if not info['episodes']:
        return {'success': False, 'error': '未找到集数信息'}
    
    # 获取第一集的播放 URL
    first_episode = info['episodes'][0]
    play_info = parser.get_play_url(video_id, first_episode['some_id'], str(first_episode['episode']))
    
    return {
        'success': True,
        'title': info['title'],
        'video_id': video_id,
        'total_episodes': len(info['episodes']),
        'episodes': info['episodes'],
        'current_episode': first_episode['episode'],
        'play_url': play_info.get('url', ''),
        'from': play_info.get('from', ''),
    }


def get_video_info(video_id: str) -> dict:
    """获取视频详细信息（标题和所有集数）
    
    Args:
        video_id: 视频 ID
        
    Returns:
        视频信息字典
    """
    parser = FengHuangVideoParser()
    return parser.get_video_info(video_id)


def get_play_url(video_id: str, some_id: str, episode: str) -> dict:
    """获取指定集数的播放 URL
    
    Args:
        video_id: 视频 ID
        some_id: 集数ID
        episode: 集数号
        
    Returns:
        播放信息字典，包含 url, from 等字段
    """
    parser = FengHuangVideoParser()
    return parser.get_play_url(video_id, some_id, episode)


__all__ = [
    'FengHuangVideoParser',
    'parse_video',
    'get_video_info',
    'get_play_url',
]
