"""
全量爬取凤凰影院视频数据

功能：
1. 遍历所有分类的所有分页
2. 将视频信息存入MySQL数据库
3. 支持断点续爬（记录爬取进度）
4. 增量更新：只爬取新增的页面
"""
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
import html as html_mod
import re
import time
import json
from datetime import datetime

# 禁用代理
requests.Session().trust_env = False
requests.Session().proxies = {"http": None, "https": None}

BASE_URL = "https://land8028.com"

# 所有分类配置
CATEGORIES = {
    '3': '综艺',
    '14': '国产剧',
    '15': '港剧',
    '16': '台湾剧',
    '17': '韩剧',
    '18': '日剧',
    '31': '泰剧',
    '19': '欧美剧',
    '20': '海外剧',
    '37': 'TV日韩',
    '38': '体育',
    '7': '动作片',
    '8': '喜剧片',
    '9': '爱情片',
    '10': '科幻片',
    '11': '恐怖片',
    '12': '剧情片',
    '13': '纪录片',
    '29': '战争片',
    '2': '古装剧',
    '1': '电影',
}

class FullCrawler:
    def __init__(self):
        self.session = requests.Session()
        self.session.verify = False
        self.session.trust_env = False
        self.session.proxies = {"http": None, "https": None}
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        })
        self.progress_file = 'crawl_progress.json'
        self.progress = self._load_progress()
    
    def _load_progress(self):
        """加载爬取进度"""
        try:
            with open(self.progress_file, 'r') as f:
                return json.load(f)
        except:
            return {'last_crawl': None, 'completed_pages': {}, 'total_crawled': 0}
    
    def _save_progress(self):
        """保存爬取进度"""
        with open(self.progress_file, 'w') as f:
            json.dump(self.progress, f, indent=2)
    
    def _get_page(self, url, timeout=15):
        """获取页面"""
        for retry in range(3):
            try:
                resp = self.session.get(url, timeout=timeout)
                if resp.status_code == 200:
                    resp.encoding = 'utf-8'
                    return html_mod.unescape(resp.text)
            except Exception as e:
                if retry == 2:
                    print(f"      请求失败: {e}")
                time.sleep(1)
        return None
    
    def _extract_video_list(self, page_text, category):
        """从页面提取视频列表"""
        results = []
        seen = set()
        
        # 模式1: 带 title 属性的链接
        pattern1 = r'<a[^>]+href=["\']([^"\']*weihu/(\d+)\.html[^"\']*)["\'][^>]*title=["\']([^"\']+)["\'][^>]*>[\s\S]*?<img[^>]+src=["\']([^"\']+)["\']'
        for url, vid, title, cover in re.findall(pattern1, page_text):
            if vid in seen:
                continue
            seen.add(vid)
            title = title.strip()
            if not title or len(title) < 2 or title.isdigit():
                continue
            if cover and not cover.startswith('http'):
                cover = BASE_URL + cover
            full_url = url if url.startswith('http') else BASE_URL + url
            results.append({
                'url': full_url,
                'video_id': vid,
                'title': title,
                'cover': cover,
                'category': category
            })
        
        # 模式2: img alt 属性
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
                    cover = BASE_URL + cover
                full_url = url if url.startswith('http') else BASE_URL + url
                results.append({
                    'url': full_url,
                    'video_id': vid,
                    'title': title,
                    'cover': cover,
                    'category': category
                })
        
        return results
    
    def _get_category_pages(self, cat_id, category):
        """获取分类的总页数"""
        first_page = f'{BASE_URL}/show/{cat_id}-----------.html'
        text = self._get_page(first_page)
        if not text:
            return 1
        
        # 找尾页
        last_match = re.search(rf'href=["\']([^"\']*show/{cat_id}--------(\d+)---[^"\']*)["\'][^>]*>(?:尾页|最后一页)', text)
        if last_match:
            return int(last_match.group(2))
        return 1
    
    def save_to_db(self, videos):
        """保存视频到数据库"""
        if not videos:
            return 0
        
        try:
            from crawagent.tools.database import get_default_db
            db = get_default_db()
            
            saved = 0
            for v in videos:
                try:
                    extra_data = {
                        'video_id': v.get('video_id', ''),
                        'cover': v.get('cover', ''),
                        'type': v.get('category', ''),
                        'status': '',
                    }
                    db.save_crawl(
                        url=v.get('url', ''),
                        title=v.get('title', ''),
                        content='',
                        source='fenghuang',
                        category=v.get('category', ''),
                        extra_data=extra_data
                    )
                    saved += 1
                except Exception as e:
                    pass  # 忽略重复等错误
            
            return saved
        except Exception as e:
            print(f"      数据库错误: {e}")
            return 0
    
    def crawl_category(self, cat_id, category, max_pages=None):
        """爬取单个分类"""
        total_pages = self._get_category_pages(cat_id, category)
        
        if max_pages:
            total_pages = min(total_pages, max_pages)
        
        print(f"  [{cat_id}] {category}: {total_pages}页")
        
        # 获取已爬取的页数
        cat_progress = self.progress['completed_pages'].get(cat_id, [])
        
        new_crawled = 0
        new_saved = 0
        
        for page_num in range(1, total_pages + 1):
            if page_num in cat_progress:
                continue  # 跳过已爬取的页面
            
            page_url = f'{BASE_URL}/show/{cat_id}--------{page_num}---.html'
            
            try:
                text = self._get_page(page_url)
                if not text:
                    continue
                
                videos = self._extract_video_list(text, category)
                saved = self.save_to_db(videos)
                
                new_crawled += 1
                new_saved += saved
                
                # 更新进度
                cat_progress.append(page_num)
                self.progress['completed_pages'][cat_id] = cat_progress
                self.progress['total_crawled'] += 1
                self._save_progress()
                
                # 进度输出
                if new_crawled % 10 == 0:
                    print(f"    已爬{new_crawled}/{total_pages}页, 新增{new_saved}个视频")
                
                # 限速
                time.sleep(0.3)
                
            except Exception as e:
                print(f"      第{page_num}页错误: {e}")
                time.sleep(1)
        
        return new_crawled, new_saved
    
    def crawl_all(self, max_pages_per_category=None):
        """全量爬取所有分类"""
        print("=" * 60)
        print("开始全量爬取凤凰影院视频数据")
        print("=" * 60)
        
        total_pages = 0
        total_videos = 0
        
        for cat_id, category in CATEGORIES.items():
            print(f"\n正在爬取: {category}...")
            pages, videos = self.crawl_category(cat_id, category, max_pages_per_category)
            total_pages += pages
            total_videos += videos
            print(f"  ✅ {category}: 爬取{pages}页, 新增{videos}个视频")
        
        self.progress['last_crawl'] = datetime.now().isoformat()
        self._save_progress()
        
        print("\n" + "=" * 60)
        print(f"全量爬取完成!")
        print(f"  总爬取页面: {total_pages}")
        print(f"  总新增视频: {total_videos}")
        print(f"  最后爬取时间: {self.progress['last_crawl']}")
        print("=" * 60)
    
    def incremental_update(self, check_pages=3):
        """增量更新：检查每个分类的最新几页"""
        print("=" * 60)
        print("增量更新：检查最新视频")
        print("=" * 60)
        
        total_new = 0
        
        for cat_id, category in CATEGORIES.items():
            total_pages = self._get_category_pages(cat_id, category)
            
            # 获取已爬取的最大页数
            cat_progress = self.progress['completed_pages'].get(cat_id, [])
            max_crawled = max(cat_progress) if cat_progress else 0
            
            # 检查最新几页
            new_pages = list(range(max(max_crawled + 1, total_pages - check_pages + 1), total_pages + 1))
            
            if new_pages:
                print(f"\n[{category}] 检查{len(new_pages)}个新页面...")
                new_videos = 0
                
                for page_num in new_pages:
                    page_url = f'{BASE_URL}/show/{cat_id}--------{page_num}---.html'
                    text = self._get_page(page_url)
                    
                    if text:
                        videos = self._extract_video_list(text, category)
                        saved = self.save_to_db(videos)
                        new_videos += saved
                        
                        # 更新进度
                        if page_num not in cat_progress:
                            cat_progress.append(page_num)
                            self.progress['completed_pages'][cat_id] = cat_progress
                            self.progress['total_crawled'] += 1
                            self._save_progress()
                        
                        time.sleep(0.3)
                
                total_new += new_videos
                if new_videos > 0:
                    print(f"  ✅ 新增{new_videos}个视频")
        
        self.progress['last_crawl'] = datetime.now().isoformat()
        self._save_progress()
        
        print("\n" + "=" * 60)
        print(f"增量更新完成: 新增{total_new}个视频")
        print("=" * 60)
    
    def show_status(self):
        """显示爬取状态"""
        print("=" * 60)
        print("爬取进度状态")
        print("=" * 60)
        
        for cat_id, category in CATEGORIES.items():
            total_pages = self._get_category_pages(cat_id, category)
            cat_progress = self.progress['completed_pages'].get(cat_id, [])
            progress_pct = len(cat_progress) / total_pages * 100 if total_pages > 0 else 0
            
            status = "✅" if progress_pct == 100 else "⏳" if progress_pct > 0 else "⬜"
            print(f"{status} [{cat_id}] {category}: {len(cat_progress)}/{total_pages}页 ({progress_pct:.1f}%)")
        
        print(f"\n总计: {self.progress['total_crawled']}个页面已爬取")
        print(f"最后爬取: {self.progress.get('last_crawl', '从未')}")


if __name__ == "__main__":
    import sys
    
    crawler = FullCrawler()
    
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "status":
            crawler.show_status()
        elif cmd == "update":
            crawler.incremental_update()
        elif cmd == "full":
            crawler.crawl_all()
        elif cmd == "test":
            # 测试：只爬每个分类前2页
            crawler.crawl_all(max_pages_per_category=2)
        else:
            print("用法: python full_crawler.py [status|update|full|test]")
    else:
        # 默认：先显示状态，然后增量更新
        crawler.show_status()
        print("\n运行 'python full_crawler.py full' 进行全量爬取")
        print("运行 'python full_crawler.py update' 进行增量更新")
