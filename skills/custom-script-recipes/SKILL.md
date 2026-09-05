---
name: custom-script-recipes
description: run_custom_script 配方库：内置helper速查（get_site_profile/browser_render/decrypt_js_eval_html/make_session）、S1-S4各级可复制骨架、JS混淆与字体加密对策、常见报错诊断。预制工具失败要写脚本时先读我。
---

# run_custom_script 配方库

system.md 定义了 S1→S4 阶梯规则（每级最多 1 段）；本技能给每级的**可复制骨架**与 helper 细节。写脚本前先读 `get_site_profile(本站)` —— 有存档直接复用旧脚本，零成本。

## Helper 速查（脚本内直接调用，无需 import）

| Helper | 用途 | 返回 | 关键细节 |
|---|---|---|---|
| `get_site_profile(origin)` | 读站点档案 | dict：`cookies/script/strategy/notes` | origin 传 `https://域名` 或完整 URL 都行；不存在的站返回 `{}` |
| `_INJECTED_PROFILES` | 同上的免调用版 | dict[origin→profile] | 脚本里出现的域名**自动**注入，循环遍历最方便 |
| `make_session(retries=3, backoff=0.5)` | 带重试的 Session | requests.Session | 自动 429/5xx 重试 + 随机 UA 轮换；S2 的主力 |
| `decrypt_js_eval_html(html)` | 解 CSDN 式 eval 混淆 | 明文 HTML 片段 | 只认 `oo=[0x..] XOR key` 模式；解不出返回 `""` —— 返回空就是别加密方式，别反复试 |
| `browser_render(url, wait_ms, extra_headers, cookies, wait_selector)` | Playwright 无头渲染 | `(final_url, html)` | 失败时 html 以 `"ERR"` 开头 —— **必须**检查；`wait_selector` 传正文 CSS 选择器能省一半等待 |
| `HAS_LXML` / `lxml_html` | 深 DOM 解析 | bool / module | 存在才用，先查 HAS_LXML |

预导入已就绪：requests、BeautifulSoup、json、re、Path、urlparse/urljoin、datetime、http 标准库等。`PROJECT_ROOT / DOWNLOADS_DIR / OUTPUT_DIR` 三个 Path 可直接用。

## S1 骨架（直连）

```python
headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
           "Referer": "https://<站点根>", "Accept-Language": "zh-CN,zh;q=0.9"}
r = requests.get(URL, headers=headers, timeout=20)
print("STATUS:", r.status_code, "LEN:", len(r.text))
soup = BeautifulSoup(r.text, "html.parser")
# 第一行先打状态与长度 —— 200 + 长度可疑(壳页面<5KB) = S1 失败信号
```

失败信号：LEN 异常短、正文选择器 None、内容里出现「请登录/验证码」。

## S2 骨架（会话+Cookie）

```python
s = make_session()
profile = get_site_profile("https://<域名>")
if profile.get("cookies"):
    s.headers["Cookie"] = profile["cookies"]      # 档案 Cookie 优先
r = s.get(URL, timeout=20)
print("LEN:", len(r.text))
```

- Cookie 仍不够 → 停，向用户要登录 Cookie（转述 F12 取法），拿到先 `save_site_profile` 再重跑 S2
- 同站第二次任务：S2 应直接从档案 Cookie 起步，S1 可跳过

## S3 骨架（混淆解密 / 深DOM）

```python
plain = decrypt_js_eval_html(r.text)
if plain:
    soup = BeautifulSoup(plain, "html.parser")
else:
    print("NOT_EVAL_OBFUSCATION")   # 声明判别过，不是这种加密
```

判别特征：响应体 < 5KB、只有一个 `<script>`、含 `eval(` / `document.write`。CSDN RSS、移动端 UA 不匹配的响应最典型。**字体加密**（正文出现私有区乱码字符）不是这个 helper 能解的 —— 如实告知用户，别烧预算。

## S4 骨架（浏览器渲染）

```python
profile = get_site_profile("https://<域名>")
final_url, html = browser_render(URL, wait_ms=5000,
    cookies=profile.get("cookies", ""),
    wait_selector="#content_views, .article, main")   # 传正文选择器
if html.startswith("ERR"):
    print("RENDER_FAIL:", html)     # 打印原因，停手向用户汇报
else:
    soup = BeautifulSoup(html, "html.parser")
    print("BODY_LEN:", len(soup.get_text(strip=True)))
```

S4 仍拿不到（VIP 遮罩、服务端鉴权）= 阶梯到顶 —— 按 system.md 给用户三选一：给 Cookie / 人工贴文 / 只存免费部分。

## 诊断对照表

| 症状 | 根因 | 下一步 |
|---|---|---|
| LEN < 5KB 且全是 script | SPA 壳 | 直接 S4，别试 S2/S3 |
| 403 但浏览器能开 | 防盗链/UA | 补 Referer + 桌面 UA → S1 重判一次 |
| 200 但正文空 | 惰性加载/JS 渲染 | 找 `data-src` 类属性 → 仍空走 S4 |
| 内容 PUA 乱码 | 字体混淆 | 停，告知用户（解密不在能力内） |
| `-2012` / 418 | 站点风控码 | 节流 sleep 1-3s；连败则等或换 P 路线 |
| 直链下载 403 | 图床防盗链 | 下载请求带 Referer=站点根 |

## 预算与收尾（重申，写脚本前默数）

- 每级最多 1 段、每目标 URL 最多 4 段脚本 —— S 级重复 = 预算违规
- 脚本成功 → `save_site_profile(script=完整代码, strategy="custom_script")`，下站复用 `recommend_scripts`
- 输出全靠 `print()`；BODY LEN / SUMMARY 这类标记行是给外层做判断用的
