# 强风控与 JS 逆向能力训练报告

> 生成时间：2026-08-02
> 目标：针对强风控 / JS 签名站点，实现**无人工干预自动一键爬取**
> 方式：浏览器 API 捕获（签名由浏览器 JS 自动计算，无需逆向算法）

## 训练结论

| 项 | 结果 |
|----|------|
| 捕获签名 API | ✅ 85~100 条/页（`/aweme/v1/web/` 系列，含 a_bogus 签名参数） |
| 提取结构化数据 | ✅ 自动加权提取（容器键/媒体字段/内容字段） |
| 提取媒体直链 | ✅ 20 条/页 douyinvod 签名直链 + 页面 `<video>` 标签 src |
| 直链下载验证 | ✅ HTTP 200、video/mp4、ftyp 有效（带 Referer 防盗链头） |
| 人工干预 | ❌ 全程无需登录、无需逆向签名算法 |

## 核心思路：浏览器即签名机

强风控站点的数据链路是：

```
站点 JS 计算签名（a_bogus / x-s 等） → 请求带签名 API → 返回 JSON 数据
```

与其人工逆向签名算法（成本高、随时失效），不如**让浏览器自己当签名机**：

```
Playwright 打开页面 → 站点 JS 自动算好签名 → 拦截 page "response" 事件
→ 拿到带签名的真实 API 响应 → 滚动触发分页 → 启发式提取数据/直链
```

## 一键爬取方式（无人工干预）

### 方式一：Agent 指令（推荐）

在对话页 / API 发出指令即可自动完成：

```bash
curl -X POST http://localhost:8000/api/harness/quick-crawl \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.douyin.com/", "instruction": "用 harvest_api 捕获抖音首页的签名 API 数据，提取视频直链并保存"}'
```

Agent 会自动调用 `harvest_api` 工具 → 捕获签名 API → 提取直链 → 保存。

### 方式二：直接调用 harvest_api 工具

```python
from crawagent.core.api_harvester import BrowserAPIHarvester

harvester = BrowserAPIHarvester()
result = await harvester.harvest(
    "https://www.douyin.com/",
    api_patterns=["/aweme/", "feed"],   # 额外 API 特征（可选）
    max_scrolls=5,                       # 滚动轮数（触发分页）
)
# result.items       → 结构化数据（自动加权提取，过滤配置类）
# result.media_urls  → 媒体直链（含签名）
# result.api_calls   → 全部捕获的 API 响应
# result.risk_detected → 风控特征检测
```

### 方式三：下载捕获到的签名直链

```python
import httpx

# 签名直链带防盗链，需 Referer + UA
headers = {
    "User-Agent": "Mozilla/5.0 ... Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://www.douyin.com/",
}
async with httpx.AsyncClient(headers=headers, trust_env=False) as client:
    r = await client.get(video_url)   # → HTTP 200, video/mp4
```

## 实测数据（2026-08-02）

### 抖音首页（API 型强风控站点）

- 捕获 API 响应：85~100 条（含 `aweme/v1/web/hot/search/list`、`social/count`、`risklevel` 等签名接口）
- 提取签名视频直链：20 条（`v26-web.douyinvod.com/...` 带时间戳签名）
- 直链下载：带 Referer → HTTP 200 / `video/mp4` / 1.3 MiB ftyp 有效 ✅
- 风控检测：无触发（正确 UA + 禁用代理 + 浏览器渲染）

### 抖音视频页

- 捕获 API：100 条
- 页面 `<video>` 标签 src（当前播放直链）：`v26-webf.douyinvod.com/...` 与 `/aweme/v1/play/?file_id=...&sign=...` ✅

## 实现模块

| 模块 | 说明 |
|------|------|
| [api_harvester.py](crawagent/core/api_harvester.py) | `BrowserAPIHarvester`：拦截响应、滚动分页、启发式提取、风控检测 |
| [tools.py](crawagent/harness/tools.py) | `harvest_api` 工具定义 + executor，Agent 一键调用 |

### 启发式提取规则

- **列表数据**：容器键（items/aweme_list）×3、含媒体字段 ×10、内容字段 ×2、普通 ×1；跳过 config/rule/abtest/statistics 等配置类子树
- **媒体直链**：扩展名（.mp4/.m3u8/.flv）+ 字段名（play_addr/url_list/uri）+ 媒体 CDN 域名（douyinvod/byteimg/kwaicdn/hdslb/aliyuncs...）
- **去重**：按 id/url 字段；media 按 URL 去重

## 局限与后续

- **私密/会员作品**：签名直链 403 时需登录态（`POST /api/auth/login`）
- **强验证码站点**：若触发滑块等交互验证码，需人工一次或接入验证码识别服务
- **签名时效**：直链有时效（抖音约 24h），批量下载应在捕获后尽快执行

## 测试

- `tests/test_api_harvester.py`：9 项（列表提取加权/配置过滤、媒体扩展名/字段/域名识别、去重）
- 全量：**146 项测试通过**
