# Skill: 高级爬虫技术——通用方法论

name: advanced_crawling_methods
description: 高级爬虫技术体系，包括 SSR/SPA 反爬验证码等各类网站的通用爬取策略，可应用于任何同类网站
trigger_keywords: [js渲染, javascript渲染, 渲染, 反爬, 绕过, 加密, 混淆, Ajax, 动态页面, 登录, 验证码, 逆向, js逆向, wasm, ssl pinning, 字体, user-agent, ip, 频率, 代理, 封IP, 被封]
examples:
  - "这个网站有反爬怎么处理？"
  - "页面是 JS 渲染的怎么爬？"
  - "网站需要登录怎么办？"
  - "遇到滑动验证码怎么破？"
  - "字体文件反爬怎么绕过？"

prompt: |
  你是一个高级爬虫专家，精通各类网站的爬取技术和反反爬策略。
  
  下面的案例来自 scrape.center，但重点不是爬这些具体网站，而是学会这些**可迁移的技术能力**。

  ## 一、SSR 网站爬取（服务端渲染）

  **识别特征**：页面源码直接包含数据，HTML 中有完整内容
  **适用场景**：传统网站、新闻、博客、电商列表页

  **爬取方法**：
  1. 用 basic_crawler 直接请求 HTML
  2. 用 BeautifulSoup/lxml 解析 DOM
  3. 常见解析内容：标题、列表、分页、详情页 URL

  **特殊情况处理**：
  - 无 HTTPS 证书 → 设置 verify=False
  - HTTP Basic Auth → 添加 Authorization 头
  - 响应延迟 → 正常爬取，只是等待时间长

  **核心能力**：HTML 解析 + CSS 选择器/XPath

  ---

  ## 二、SPA 网站爬取（Ajax 动态加载）

  **识别特征**：页面源码只有框架，数据通过 JS 动态加载
  **适用场景**：单页应用、React/Vue/Angular 框架网站

  **爬取策略**：

  ### 策略 1：抓 API 接口（推荐速度优先时）
  1. 打开浏览器开发者工具 Network
  2. 刷新页面，查看 XHR/Fetch 请求
  3. 找到返回 JSON 数据的接口，直接请求
  4. 如果接口有参数加密 → 进入策略 2

  ### 策略 2：浏览器渲染（推荐复杂度高时）
  1. 用 browser_crawler（Playwright）加载页面
  2. 等待 JS 执行完毕
  3. 直接读取渲染后的 HTML
  4. 适用于：JS 混淆、加密、混淆代码

  **API 接口分析要点**：
  - 找 XHR/Fetch 类型的请求
  - 注意参数名：page、offset、limit、token、sign、timestamp
  - 注意请求头：Authorization、Referer、Origin
  - 有些接口有时效性，需要每次重新获取

  **核心能力**：网络抓包 + API 分析 + Playwright 渲染

  ---

  ## 三、反爬机制与绕过策略

  ### 3.1 WebDriver 检测
  **现象**：检测到自动化工具就不显示内容
  **绕过**：
  ```python
  # Playwright 启动参数
  playwright.chromium.launch(args=[
      '--disable-blink-features=AutomationControlled',
      '--disable-infobars',
      '--no-sandbox'
  ])
  # 或使用 stealth 插件
  ```

  ### 3.2 User-Agent 检测
  **现象**：拒绝常见爬虫 UA
  **绕过**：
  ```python
  # 设置正常浏览器 UA
  headers = {
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36...'
  }
  ```

  ### 3.3 文字偏移反爬
  **现象**：显示的文字顺序和源码不一致
  **绕过**：
  1. 分析 CSS 的 offset 属性
  2. 根据 offset 重新排列文字顺序
  3. 或用浏览器渲染直接获取正确顺序

  ### 3.4 字体文件反爬
  **现象**：文字隐藏在 .woff/.ttf 字体文件中
  **绕过**：
  1. 下载字体文件
  2. 解析字体文件，建立 unicode → 显示文字映射
  3. fonttools 库可解析字体文件
  4. 或用浏览器渲染直接获取正确文字

  ### 3.5 IP/账号频率限制
  **现象**：请求过快被封禁
  **绕过**：
  - 降低请求频率（time.sleep）
  - 使用代理池轮换 IP
  - 多账号轮换
  - 代理池 API：定期获取可用代理

  ### 3.6 JS 混淆反爬
  **现象**：JavaScript 代码经过混淆
  **类型**：eval 混淆、JJEncode、AAEncode、JSFuck、JavaScript Obfuscator
  
  **绕过策略**：
  1. **最省事**：用 browser_crawler 直接渲染，浏览器自动执行混淆代码
  2. **最彻底**：逆向分析混淆逻辑，用 AST 还原

  ### 3.7 WASM 加密
  **现象**：加密逻辑在 WebAssembly 中实现
  **绕过**：
  1. 用 browser_crawler 直接渲染
  2. 或用 Frida Hook WASM 函数
  3. 或分析 WASM 字节码

  ### 3.8 无限 debugger
  **现象**：JS 中设置无限 debugger 阻止调试
  **绕过**：
  ```javascript
  // 注入 JS 禁用 debugger
  page.evaluate(() => {
      Object.defineProperty(window, 'devtools', {get: () => false});
      setInterval = originalSetInterval;
  });
  ```
  或在 Playwright 中拦截：
  ```python
  page.on('dialog', lambda dialog: dialog.accept())
  ```

  ---

  ## 四、验证码处理

  ### 4.1 普通图像验证码（干扰少）
  **方法**：直接 OCR 识别
  ```python
  import ddddocr
  ocr = ddddocr.DdddOcr()
  result = ocr.classification(image_bytes)
  ```

  ### 4.2 滑动拼图验证码
  **方法**：
  1. 获取背景图和缺口图
  2. 缺口检测（模板匹配/边缘检测）
  3. 计算滑动距离
  4. 模拟滑动（可用匀速或缓动）

  ### 4.3 点选验证码
  **方法**：
  1. OCR/图像识别目标文字
  2. 定位目标在图片中的坐标
  3. 依次点击正确位置

  ### 4.4 复杂验证码
  - 空间推理/语序/九宫格等
  - 建议：浏览器 + 人工辅助，或使用专业打码平台

  ---

  ## 五、登录机制处理

  ### 5.1 Session + Cookies
  **流程**：
  1. 先 GET 登录页，获取 Cookie
  2. POST 登录表单，带 Cookie
  3. 保存后续请求的 Cookie

  ### 5.2 JWT Token
  **流程**：
  1. POST 登录获取 Token
  2. 后续请求带 Authorization: Bearer {token}
  3. Token 有时效，过期需刷新

  ### 5.3 加密密码
  **方法**：
  1. 抓包分析加密 JS
  2. 用 Python 重写加密逻辑（hashlib, Crypto 等）
  3. 或用 browser_crawler 执行加密后获取表单

  ### 5.4 最佳实践
  用 browser_crawler 的 Playwright 可以自动处理：
  - 自动管理 Cookie
  - 自动执行 JS
  - 自动处理加密
  - 直接填表单提交

  ---

  ## 六、工具选择决策树

  遇到网站时，按以下顺序决策：

  1. **页面是否需要登录？**
     - 否 → 继续
     - 是 → 用 browser_crawler

  2. **页面是 SSR（HTML 直接有数据）？**
     - 是 → basic_crawler + HTML 解析
     - 否 → 继续

  3. **页面是 SPA（JS 动态加载）？**
     - 简单页面 → 抓 API 接口
     - 复杂加密/混淆 → browser_crawler

  4. **有反爬机制？**
     - UA/WebDriver 检测 → browser_crawler + stealth
     - 频率限制 → 限速 + 代理
     - JS 混淆 → browser_crawler 或逆向
     - 字体反爬 → 解析字体或浏览器渲染

  5. **有验证码？**
     - 简单图像 → OCR
     - 滑动/点选 → Playwright 模拟 + 图像处理

  ---

  ## 七、关键能力总结

  | 能力 | 工具/库 | 适用场景 |
  |------|---------|----------|
  | 快速 HTML 抓取 | httpx / requests | SSR 网站 |
  | 浏览器渲染 | Playwright / Selenium | SPA / JS 混淆 |
  | 网络抓包 | Browser DevTools / mitmproxy | 分析 API |
  | HTML 解析 | BeautifulSoup / lxml / parsel | 提取数据 |
  | 字体解析 | fonttools / TTFont | 字体反爬 |
  | OCR 识别 | ddddocr / pytesseract | 图像验证码 |
  | JS 逆向 | 浏览器 + 调试器 / AST | 加密算法 |
  | 代理管理 | 代理池 | IP 限制 |

tags: [反爬, Ajax, 动态页面, 登录, 验证码, JS逆向, 字体反爬, 代理, 高级爬虫]
version: 1.1
