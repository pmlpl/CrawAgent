# CrawAgent 文档索引

LLM 驱动的智能爬虫 Agent 框架 —— LangChain + LangGraph + DeepSeek + FastAPI + Vue 3

| 文档 | 内容 | 适合谁看 |
|------|------|---------|
| [00-项目介绍](./00-项目介绍.md) | 项目定位、核心能力、快速开始、缺陷分析总评 | 初次接触项目 |
| [01-架构总览](./01-架构总览.md) | 分层架构、技术栈、整体设计思想 | 初次接触项目 |
| [02-项目结构](./02-项目结构.md) | 完整目录树 + 每个文件的职责 | 想快速定位代码 |
| [03-核心机制](./03-核心机制.md) | Agent 循环、双中间件、置信度自动升级、事件日志重放、会话持久化 | 理解"怎么跑起来的" |
| [04-工具层详解](./04-工具层详解.md) | 18 个工具的功能、签名、适用场景 | 选工具用 / 改工具 |
| [05-扩展指南](./05-扩展指南.md) | 加新工具、加中间件、加 API、换 LLM、WS 事件协议 | 二次开发必读 |
| [06-配置与运行](./06-配置与运行.md) | .env 全字段说明、启动命令、首次安装清单 | 部署 / 环境配置 |
| [07-待办问题清单](./07-待办问题清单.md) | P0/P1/P2 分级的问题与 TODO | 接手后续开发 |
| [08-系统架构设计](./08-系统架构设计.md) | 分层职责、核心模块、数据流、设计决策与风险清单 | 理解"为什么这么设计" |

配套架构图：[01-分层架构](./architecture/01-分层架构.svg) · [02-单轮数据流](./architecture/02-单轮数据流.svg) · [03-降级链路](./architecture/03-降级链路.svg) · [04-裁剪策略对比](./architecture/04-裁剪策略对比.svg)

## 建议阅读顺序

```
00 → 01 → 08（先建立结构认知）→ 02 → 03 → 04 → 05（动手改代码前必读）→ 07（领任务）
```

## 五分钟速览

- **是什么**：用户给一个 URL 或任务描述，LLM（DeepSeek）自主决定调用哪个工具（爬取/提取/写脚本/保存…），循环直到完成任务
- **怎么用**：`python -m crawagent.web.server` → http://127.0.0.1:8000
- **核心文件**：
  - `crawagent/graph/agent.py` — Agent 装配 + System Prompt（18 工具 + 双中间件）
  - `crawagent/graph/middleware.py` — TrimHistoryMiddleware（token 裁剪）
  - `crawagent/graph/script_forcer.py` — ScriptForcerMiddleware（防死循环）
  - `crawagent/tools/*.py` — 18 个工具
  - `crawagent/web/server.py` — FastAPI + WebSocket
  - `crawagent/config/settings.py` — 全部配置
- **亮点**：
  - 置信度评分自动升级（静态 HTTP → Playwright 浏览器 → 自写脚本）
  - 失败 3 次强制切换 `run_custom_script`（防死循环中间件）
  - DeepSeek prompt cache 优化：静态 system prompt + **半水位淘汰**裁剪 + 强制消息追加末尾（旧的滑动窗口方案实测命中率仅 37.5%）
  - WebSocket 流式输出 + 事件日志断线重放（`done` 不丢）
  - 提取结果统一转 Markdown（保留标题层级 / 代码块 / 列表 / 表格 / 链接）
  - 子 Agent 进度实时上报（搜索 / 探测阶段不再黑屏）
