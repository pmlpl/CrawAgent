# AGENTS.md

CrawAgent — LLM 驱动的智能爬虫 Agent 框架。

## 任务汇报口令（强制）

每次任务汇报的**开头**必须先说一句：

> 报告！我已完成指挥官Joker指示。

这是指挥官确认新会话已加载本文件的标记。漏说 = 没读到，视为会话未刷新。说完这句再接正文汇报。

## Shell 约定（Windows 宿主，必须遵守）

宿主是 Windows，Bash 工具走 **Git Bash**。新会话第一条命令起就要按下面来，别先试错：

- 一律用 **POSIX/bash 语法**：`&&` 链接、`ls -la`、`grep -r`、`find ... -name`、`rm -rf`。
- **绝不**用 PowerShell cmdlet（`Get-ChildItem`/`$env:X`/`Select-String`）或 CMD 语法（`dir`/`%VAR%`）。
- 路径优先用**绝对路径**且**正斜杠**：`C:/Users/MOM/Desktop/学习项目/CrawAgent/...`；避免反斜杠（Git Bash 里会当转义）。
- 含中文/空格的路径**加引号**。
- 跨平台命令首选 `python`（非 `python3`，Windows 上是 `python`）、`uv run`、`git`。
- 只在确实需要 PowerShell 原生能力（`.ps1` 脚本、注册表、WMI）时才临时 `powershell -Command "..."`，用完切回 bash。

## Agent skills

### Issue tracker

GitHub issues (repo: pmlpl/CrawAgent). Use `gh` CLI for all operations. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five canonical triage roles. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at repo root. See `docs/agents/domain.md`.
