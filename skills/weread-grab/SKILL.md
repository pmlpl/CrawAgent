---
name: weread-grab
description: 微信读书/weread.qq.com 抓取攻略：wr_vid+wr_ssk Cookie 获取与存档、章节列表、逐章正文、VIP章节处理、整本书批量导出Markdown。任务涉及微信读书/微读时先读我。
---

# 微信读书（weread.qq.com）抓取攻略

## 硬前提：登录 Cookie

微读**所有**内容（章节列表+正文）都要登录态。`crawl_webpage` / `browse_and_crawl` 在这个站只会拿到登录页或空壳 —— **跳过它们**，直接用专用工具。

Cookie 拿法（转述给用户）：
1. 浏览器登录 https://weread.qq.com/
2. F12 → Application → Cookies → 复制 `wr_vid` 和 `wr_ssk` 两个值
3. 把 `wr_vid=xxx; wr_ssk=yyy` 发给你（不要让用户发到别处）

拿到后：`save_site_profile(origin="https://weread.qq.com", notes="cookie: <完整串>")`。Cookie 属敏感信息，**回复正文里不要回显**，只说「已存档」。

- 工具报 `[WEREAD_COOKIE_NOT_SET]` → 按上面话术要 Cookie
- 报 `[AUTH_FAILED]` → Cookie 过期，让用户重新复制一次并更新档案

## 标准流程（单章/几章）

```
1. list_weread_chapters(书籍详情URL或bookId)   → 章节索引、chapterUid、VIP 标记
2. get_weread_chapter(book_v, chapter_uid)      → 单章全文（book_v 从详情页 URL 拿）
3. save_to_file("第N章_章名.md", content)       → 逐章存 Markdown
```

`list_weread_chapters` 接受详情页 URL（`book-detail?type=1&v=...`）或纯数字 bookId；`get_weread_chapter` 的 `book_v` 是 URL 里 `v=` 参数（一串无连字符小写 hex），不是 bookId —— 从列表工具返回里抄，别自己拼。

## 整本书导出（批量铁律）

用户要「整本书/全部章节」→ **一个** `run_custom_script` 内循环全部免费章节，禁止逐章调 get_weread_chapter（30 章 = 30 次调用，BATCH-FIRST 违规）：

```python
profile = get_site_profile("https://weread.qq.com")
headers = {"User-Agent": "Mozilla/5.0 ...", "Cookie": profile.get("cookies", "")}
# 章节清单从 list_weread_chapters 的返回解析出 (book_v, chapter_uid, title) 元组列表
results = []
for bv, cid, title in chapters:
    r = requests.get("https://weread.qq.com/web/book/getBookReader",
                     params={"vid": bv, "cid": str(cid)}, headers=headers, timeout=20)
    data = r.json()
    # data["chapterView"]["content"] 或按实际返回结构取正文；失败就记录 skip
    results.append((title, len(text)))
    time.sleep(random.uniform(0.8, 1.5))   # 节流，防风控
print(json.dumps({t: n for t, n in results}, ensure_ascii=False))
```

- 脚本只做**提取与长度校验**；成稿落盘用第二个批量段或直接脚本里写 `OUTPUT_DIR / "<书名>/第N章_章名.md"`
- 正文若出现乱码字符块 → 字体混淆（wprec / 微读自定义字形表），这超出脚本阶梯常规级别；如实告知用户该章为加密排版，建议人工处理。**不要**把乱码存档

## VIP 章节处理

- 章节列表里 VIP 标记为真的章：`get_weread_chapter` 只会返回试读或直接报错
- 正确姿势（system.md 混合章节规则）：**先**一个批量脚本把全部免费章节处理完存档，**然后**单独汇报哪些章节是 VIP 锁定；不把 4 段脚本预算烧在锁章上
- 全本 VIP / 用户坚持要 VIP 内容 → 唯一路径是用户提供大会员账号 Cookie；明确说「服务端鉴权内容，没有公开镜像」，让用户选择：(a) 提供 VIP Cookie (b) 只导出免费部分

## 已知坑速查

- 微读 API 对同 IP 高频请求返回 `-2012` 风控码 → 脚本必须节流；连续失败时等几分钟再试，别死循环
- `book-detail` 页是 SPA，直接抓是空壳 —— 这就是为什么用 API 工具而不是网页解析
- 章节正文的换行是硬编码 `\n`，转 Markdown 时段落间补空行
- 书籍目录接口分「全书目录」与「已购/免费」两个视角 —— 列表里出现的章节 ≠ 都能读，以 get 时实际返回为准
