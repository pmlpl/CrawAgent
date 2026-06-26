# Phase 2: 浏览器自动化 - 设计文档

**日期**: 2026-06-20
**目标**: 为 CrawAgent 添加 Playwright 浏览器自动化能力，支持 JS 渲染页面的爬取
**状态**: 设计完成，待实现

---

## 1. 设计目标

- **自动检测**: 优先使用 httpx，检测到 JS 渲染/反爬时自动切换 Playwright
- **用户透明**: 用户无需关心底层用的是什么技术
- **可调试**: 支持 `/debug` 命令开启有头模式观察浏览器行为
- **可扩展**: 解耦设计，方便后续添加更多浏览器功能

---

## 2. 架构设计

### 2.1 整体架构

```
用户输入 → LangGraph 工作流
                ↓
         [Crawler Facade]  ← 统一入口
           /          \
    [BaseCrawler]  [BrowserCrawler]
      (httpx)        (Playwright)
```

### 2.2 新增文件

| 文件路径 | 职责 |
|----------|------|
| `crawagent/tools/browser_crawler.py` | Playwright 封装，处理动态内容 |
| `crawagent/tools/crawler.py` | Facade 统一入口，内部调度 httpx / Playwright |

### 2.3 修改文件

| 文件路径 | 改动内容 |
|----------|----------|
| `crawagent/tools/__init__.py` | 导出 `BrowserCrawler`、`Crawler` |
| `crawagent/graph/workflow.py` | `node_crawl` 支持自动 fallback |
| `crawagent/ui/terminal.py` | 新增 `/debug` 命令 |
| `requirements.txt` | 添加 `playwright` 依赖 |

---

## 3. 组件设计

### 3.1 Crawler Facade (crawler.py)

```python
class Crawler:
    """统一爬虫入口"""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._base = BaseCrawler()
        self._browser: BrowserCrawler | None = None

    def fetch(self, url: str) -> CrawlResult:
        # 1. 先用 httpx 快速尝试
        result = self._base.fetch(url)

        # 2. 检测是否需要浏览器
        if self._need_browser(result):
            result = self._fetch_with_browser(url)

        return result

    def _need_browser(self, result: CrawlResult) -> bool:
        """检测是否需要浏览器渲染"""
        if not result.success:
            return False

        # 触发条件
        if result.status_code in (418, 403):
            return True

        if len(result.text) < 500:
            return True

        # JS 特征检测
        text_lower = result.text.lower()
        js_indicators = ['id="app"', 'id="root"', 'data-v-app', '__nuxt', '__NEXT_DATA__']
        if any(indicator in text_lower for indicator in js_indicators):
            return True

        return False
```

### 3.2 BrowserCrawler (browser_crawler.py)

```python
class BrowserCrawler:
    """Playwright 浏览器爬虫"""

    def __init__(
        self,
        headless: bool = True,
        timeout: float = 30.0,
        scroll_pause: float = 1.0,
        max_scrolls: int = 5,
    ):
        self.headless = headless
        self.timeout = timeout
        self.scroll_pause = scroll_pause
        self.max_scrolls = max_scrolls
        self._browser = None
        self._context = None

    def fetch(self, url: str) -> CrawlResult:
        """执行浏览器爬取"""
        result = CrawlResult(url=url)

        try:
            page = self._get_page()
            page.goto(url, wait_until="networkidle", timeout=self.timeout * 1000)

            # 滚动加载
            self._scroll_to_load(page)

            # 点击加载更多
            self._click_load_more(page)

            # 获取结果
            result.success = True
            result.status_code = 200
            result.html = page.content()
            result.title = page.title()
            result.text = page.inner_text("body")

        except Exception as e:
            result.error = str(e)

        return result

    def _scroll_to_load(self, page) -> None:
        """滚动页面触发懒加载"""
        last_height = 0
        for _ in range(self.max_scrolls):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(self.scroll_pause)
            new_height = page.evaluate("document.body.scrollHeight")
            if new_height == last_height:
                break
            last_height = new_height

    def _click_load_more(self, page) -> None:
        """点击加载更多按钮"""
        selectors = [
            "button:has-text('加载更多')",
            "a:has-text('加载更多')",
            "[class*='load-more']",
            "[class*='loadMore']",
            "[id*='load-more']",
        ]
        for selector in selectors:
            if page.query_selector(selector):
                page.click(selector)
                time.sleep(self.scroll_pause)
```

### 3.3 数据流

```
用户: "爬取 https://example.com"

↓

workflow.node_crawl()

↓

Crawler().fetch(url)
    ├→ BaseCrawler().fetch()  # 快速尝试
    │   └→ 返回结果
    │
    └→ _need_browser() 检测
        ├→ False → 返回 BaseCrawler 结果
        └→ True → BrowserCrawler().fetch()
            ├→ 启动 Playwright (无头)
            ├→ page.goto()
            ├→ _scroll_to_load()
            ├→ _click_load_more()
            └→ 返回渲染后的内容

↓

工作流继续: extract_article_list() → node_summarize() → 输出
```

---

## 4. 工作流集成

### 4.1 修改 node_crawl

```python
def node_crawl(state: CrawState) -> CrawState:
    """执行爬取 - 支持自动 fallback"""
    if state.intent == "chat":
        return state

    if not state.target_urls:
        state.errors.append("没有在你的请求中找到可爬取的 URL")
        return state

    # 使用统一入口 Crawler
    crawler = Crawler(headless=not state.debug_mode)

    for url in state.target_urls:
        result = crawler.fetch(url)
        state.crawl_results.append(result)
        # ... 后续处理
```

### 4.2 Debug 模式

```python
@dataclass
class CrawState:
    # ... 其他字段
    debug_mode: bool = False  # 新增: 是否开启有头调试
```

---

## 5. 用户命令

### 5.1 新增命令

| 命令 | 功能 |
|------|------|
| `/debug` | 切换 debug 模式（开启有头浏览器） |
| `/browser on` | 强制使用浏览器模式 |
| `/browser off` | 禁用浏览器模式（只用 httpx） |

### 5.2 /skills 输出更新

```
Phase 1: 基础请求爬虫    httpx + UA 伪装 + 静态解析     ✅ 已解锁
Phase 2: 浏览器自动化    Playwright 无头 + 动态内容      ✅ 已解锁
Phase 3: AI 智能解析     LLM 自动识别页面结构            ⏳ 待解锁
...
```

---

## 6. 依赖更新

### requirements.txt 新增

```
# ---- 浏览器自动化 ----
playwright>=1.40.0
```

安装命令:
```bash
pip install playwright
playwright install chromium  # 安装 Chromium 浏览器
```

---

## 7. 错误处理

| 场景 | 处理方式 |
|------|----------|
| Playwright 未安装 | 提示用户安装，显示命令 |
| 浏览器启动失败 | 降级到 httpx，显示警告 |
| 超时 | 重试 1 次后降级 |
| 页面加载失败 | 记录错误，继续处理其他 URL |

---

## 8. 验收标准

1. **自动切换**: 访问 JS 渲染页面时自动使用 Playwright
2. **滚动加载**: 微博/Twitter 类无限滚动页面能完整爬取
3. **点击展开**: "加载更多"按钮能自动点击
4. **无头模式**: 默认不显示浏览器窗口
5. **Debug 模式**: `/debug` 能看到浏览器操作过程
6. **向后兼容**: 纯静态页面仍然使用 httpx，性能不受影响

---

## 9. 后续扩展点

- Phase 3: LLM 判断是否需要滚动/点击
- Phase 4: 浏览器指纹随机化
- Phase 5: 拦截/修改 JS 请求，Hook 加密函数
