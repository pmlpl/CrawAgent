"""提示词优化子智能体 — 分析归档会话日志，生成系统提示词优化建议。

独立分析脚本，由 Trae 的 @prompt-optimizer 技能调用。
不属于 CrawAgent 核心运行时，放在 scripts/ 目录下。

两层架构：
1. 统计层（纯 Python）：解析 logs/*.json → 工具调用成功率/循环模式/意图分布
2. 语义层（LLM）：把统计摘要喂给 LLM → 结构化的提示词修改建议

设计原则：
- 轻量：不需要 LangGraph agent loop，一次 LLM 调用即可
- 安全：只建议修改，不自动改 agent.py
- 可靠：原始 JSON 不直接塞给 LLM，先做统计压缩
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

# 当作为独立脚本运行时，确保项目根目录在 sys.path 中
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from crawagent.llm.model import get_llm

_LOGS_DIR = _PROJECT_ROOT / "logs"
_MIN_SESSIONS_FOR_ANALYSIS = 5


# ─── 统计层 ──────────────────────────────────────────────────────────────────────

def _load_archives() -> list[dict[str, Any]]:
    """加载 logs/ 下所有归档 JSON 文件。"""
    archives = []
    if not _LOGS_DIR.exists():
        return archives
    for f in sorted(_LOGS_DIR.glob("*.json")):
        if f.name.startswith("crawagent"):
            continue
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, list) and data:
                archives.append({"file": f.name, "messages": data})
        except Exception:
            continue
    return archives


def _extract_tool_calls(messages: list[dict]) -> list[dict[str, Any]]:
    """从 AI 消息中提取工具调用。"""
    calls = []
    for msg in messages:
        if msg.get("kind") == "ai" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                calls.append({
                    "name": tc.get("name", "?"),
                    "args": tc.get("args", {}),
                })
    return calls


def _extract_tool_results(messages: list[dict]) -> list[str]:
    """提取工具返回内容（用于检测失败/错误模式）。"""
    results = []
    for msg in messages:
        if msg.get("kind") == "tool":
            content = msg.get("content", "")
            if isinstance(content, str):
                results.append(content[:200])  # 截断
    return results


def _detect_loops(messages: list[dict]) -> list[dict[str, Any]]:
    """检测工具调用循环模式（连续同工具调用合并为游程，避免窗口冗余）。"""
    calls = _extract_tool_calls(messages)
    if len(calls) < 3:
        return []
    loops = []
    # 游程检测：连续同一工具 ≥ 3 次记为一次循环事件
    run_start = 0
    for i in range(1, len(calls) + 1):
        if i == len(calls) or calls[i]["name"] != calls[run_start]["name"]:
            run_len = i - run_start
            if run_len >= 3:
                loops.append({
                    "tool": calls[run_start]["name"],
                    "count": run_len,
                    "position": run_start,
                })
            run_start = i
    # 同工具总计 ≥ 5 次（跨游程的高频使用）
    tool_counts = Counter(c["name"] for c in calls)
    for tool, count in tool_counts.items():
        if count >= 5:
            loops.append({"tool": tool, "count": count, "pattern": "high_total"})
    return loops


def _extract_user_intents(messages: list[dict]) -> list[str]:
    """提取用户意图关键词。"""
    intents = []
    for msg in messages:
        if msg.get("kind") == "human":
            content = msg.get("content", "")
            if isinstance(content, str) and content.strip():
                intents.append(content[:100])
    return intents


def _detect_failure_patterns(messages: list[dict]) -> list[str]:
    """检测工具返回的失败/错误模式。"""
    failures = []
    for msg in messages:
        if msg.get("kind") != "tool":
            continue
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue
        lower = content.lower()
        patterns = [
            ("spa_shell", r"spa shell|spa壳|<5000|shell detected"),
            ("confidence_low", r"low confidence|置信度.*低|score.*<.*60"),
            ("empty_content", r"空内容|empty.*content|no.*content|内容.*为空"),
            ("blocked", r"blocked|被拦截|anti.crawl|rate.limit|429|forbidden"),
            ("login_required", r"login|登录|auth.*fail|need.*login"),
            ("download_failed", r"download.*fail|下载.*失败|merge.*fail|ffmpeg.*not"),
            ("network_error", r"timeout|timed.out|connection.*refused|winerror.*10061"),
        ]
        for name, regex in patterns:
            if re.search(regex, lower):
                failures.append(name)
    return failures


def compute_statistics(archives: list[dict[str, Any]]) -> dict[str, Any]:
    """统计层：从归档数据中提取可量化的指标。"""
    if not archives:
        return {"error": "NO_DATA", "message": "logs/ 目录为空，请先积累会话数据"}

    total_sessions = len(archives)
    all_tool_calls: list[dict[str, Any]] = []
    all_loops: list[dict[str, Any]] = []
    all_intents: list[str] = []
    all_failures: list[str] = []
    per_session_details: list[dict[str, Any]] = []

    for arch in archives:
        msgs = arch["messages"]
        calls = _extract_tool_calls(msgs)
        loops = _detect_loops(msgs)
        intents = _extract_user_intents(msgs)
        failures = _detect_failure_patterns(msgs)

        all_tool_calls.extend(calls)
        all_loops.extend(loops)
        all_intents.extend(intents)
        all_failures.extend(failures)

        per_session_details.append({
            "file": arch["file"],
            "total_messages": len(msgs),
            "tool_calls": len(calls),
            "loops": loops,
            "failures": failures,
            "intents": intents[:3],  # 最多展示 3 条
        })

    # 工具调用统计
    tool_counter = Counter(c["name"] for c in all_tool_calls)
    tool_stats = {}
    for tool, count in tool_counter.items():
        tool_stats[tool] = {
            "count": count,
            "pct": round(count / len(all_tool_calls) * 100, 1) if all_tool_calls else 0,
        }

    # 失败模式统计
    failure_counter = Counter(all_failures)
    # 循环模式统计
    loop_tools = Counter(l["tool"] for l in all_loops)
    # 循环会话数：按文件名去重
    loop_session_files = set()
    for detail in per_session_details:
        if detail["loops"]:
            loop_session_files.add(detail["file"])
    # 用户意图关键词（简单提取）
    intent_keywords = Counter()
    for intent in all_intents:
        # 提取 URL 域名作为意图线索
        domains = re.findall(r'https?://([^/]+)', intent)
        for d in domains:
            intent_keywords[d] += 1
        # 提取中文关键词
        keywords = re.findall(r'[\u4e00-\u9fff]{2,}', intent)
        for kw in keywords:
            if len(kw) >= 2:
                intent_keywords[kw] += 1

    return {
        "total_sessions": total_sessions,
        "total_tool_calls": len(all_tool_calls),
        "unique_tools": len(tool_counter),
        "tool_usage": dict(sorted(tool_stats.items(), key=lambda x: -x[1]["count"])),
        "failure_patterns": dict(failure_counter.most_common(20)),
        "loop_tools": dict(loop_tools.most_common(10)),
        "intent_domains": dict(intent_keywords.most_common(15)),
        "sessions_with_loops": len(loop_session_files),
        "sessions_with_failures": len(set(f["file"] for f in per_session_details if f["failures"])),
        "per_session": per_session_details,
    }


# ─── 语义层 ──────────────────────────────────────────────────────────────────────

_OPTIMIZER_SYSTEM_PROMPT = """You are a CrawAgent prompt optimization analyst.
Your task: Given statistics about how CrawAgent's tools are actually used in real conversations,
generate concrete, actionable suggestions for improving the system prompt in agent.py.

The system prompt currently has these sections:
1. CAPABILITIES (what the agent can do)
2. Workflow rules (workflow for general, social media, wallpaper, weread, site profile)
3. RULES (hard rules for tool usage, session scope, download paths, supervisor, etc.)

Your suggestions must be specific:
- Which section/rule to modify
- What the current behavior is (from the statistics)
- What change to suggest
- Why this change would help (evidence from the data)

Be concise. Output as a structured JSON array only.
Each suggestion: {
  "section": "CAPABILITIES" | "WORKFLOW" | "RULES",
  "target": "The specific rule or section name",
  "current_behavior": "What the data shows about current behavior",
  "suggested_change": "Concrete text to add/modify",
  "reasoning": "Why this helps, with evidence count",
  "priority": "HIGH" | "MEDIUM" | "LOW"
}
"""


def _build_llm_prompt(stats: dict[str, Any]) -> str:
    """将统计数据压缩为 LLM 可消化的提示。"""
    lines = [
        f"## CrawAgent Usage Statistics",
        f"- Total sessions analyzed: {stats['total_sessions']}",
        f"- Total tool calls: {stats['total_tool_calls']}",
        f"- Unique tools used: {stats['unique_tools']}",
        f"- Sessions with tool loops: {stats.get('sessions_with_loops', 0)}",
        f"- Sessions with failures: {stats.get('sessions_with_failures', 0)}",
        "",
        f"## Tool Usage Distribution",
    ]
    for tool, info in stats.get("tool_usage", {}).items():
        lines.append(f"- {tool}: {info['count']} calls ({info['pct']}%)")

    if stats.get("failure_patterns"):
        lines.append("")
        lines.append("## Failure Patterns Detected")
        for pattern, count in stats["failure_patterns"].items():
            lines.append(f"- {pattern}: {count} occurrences")

    if stats.get("loop_tools"):
        lines.append("")
        lines.append("## Tools with Loop Behavior")
        for tool, count in stats["loop_tools"].items():
            lines.append(f"- {tool}: {count} loop incidents")

    if stats.get("intent_domains"):
        lines.append("")
        lines.append("## Top Domains/Topics")
        for domain, count in list(stats["intent_domains"].items())[:10]:
            lines.append(f"- {domain}: {count} sessions")

    return "\n".join(lines)


def analyze_and_suggest() -> dict[str, Any]:
    """主入口：统计分析 + LLM 建议生成。"""
    archives = _load_archives()
    if len(archives) < _MIN_SESSIONS_FOR_ANALYSIS:
        return {
            "ok": False,
            "error": "INSUFFICIENT_DATA",
            "message": f"归档会话不足 {_MIN_SESSIONS_FOR_ANALYSIS} 个（当前 {len(archives)}）。"
                      f"请先积累更多会话数据后再分析。",
            "total_archives": len(archives),
        }

    stats = compute_statistics(archives)
    if "error" in stats:
        return {"ok": False, "error": stats["error"], "message": stats["message"]}

    # 构建 LLM 提示
    llm_prompt = _build_llm_prompt(stats)

    # 调用 LLM 生成建议
    try:
        llm = get_llm()
        response = llm.invoke([
            {"role": "system", "content": _OPTIMIZER_SYSTEM_PROMPT},
            {"role": "user", "content": llm_prompt},
        ])
        raw_text = response.content if hasattr(response, "content") else str(response)

        # 尝试解析 JSON
        suggestions = []
        try:
            # 尝试直接解析
            parsed = json.loads(raw_text.strip())
            if isinstance(parsed, list):
                suggestions = parsed
            elif isinstance(parsed, dict) and "suggestions" in parsed:
                suggestions = parsed["suggestions"]
        except json.JSONDecodeError:
            # 尝试提取 JSON 块
            match = re.search(r'\[[\s\S]*\]', raw_text)
            if match:
                try:
                    suggestions = json.loads(match.group())
                except json.JSONDecodeError:
                    pass

        return {
            "ok": True,
            "stats": {
                "total_sessions": stats["total_sessions"],
                "total_tool_calls": stats["total_tool_calls"],
                "unique_tools": stats["unique_tools"],
                "top_tools": dict(list(stats.get("tool_usage", {}).items())[:5]),
                "failure_patterns": stats.get("failure_patterns", {}),
                "loop_tools": stats.get("loop_tools", {}),
                "sessions_with_loops": stats.get("sessions_with_loops", 0),
                "sessions_with_failures": stats.get("sessions_with_failures", 0),
            },
            "suggestions": suggestions,
            "raw_response": raw_text[:2000],  # 截断
        }
    except Exception as e:
        return {
            "ok": False,
            "error": "LLM_ANALYSIS_FAILED",
            "message": f"LLM 分析失败: {e}",
            "stats_available": True,
            "stats": {
                "total_sessions": stats["total_sessions"],
                "total_tool_calls": stats["total_tool_calls"],
                "failure_patterns": stats.get("failure_patterns", {}),
                "loop_tools": stats.get("loop_tools", {}),
            },
        }


# ─── 报告生成 + CLI ──────────────────────────────────────────────────────────────

def _render_markdown(report: dict[str, Any]) -> str:
    """将分析报告渲染为 Markdown，便于人工阅读。"""
    lines = [
        "# CrawAgent 提示词优化分析报告",
        "",
        f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]
    if not report.get("ok"):
        lines.append(f"**分析未执行**：{report.get('message', report.get('error', '未知错误'))}")
        return "\n".join(lines)

    s = report.get("stats", {})
    lines += [
        "## 使用统计",
        "",
        f"- 归档会话数：{s.get('total_sessions', 0)}",
        f"- 工具调用总数：{s.get('total_tool_calls', 0)}",
        f"- 出现循环的会话：{s.get('sessions_with_loops', 0)}",
        f"- 出现失败的会话：{s.get('sessions_with_failures', 0)}",
        "",
        "### 高频工具",
        "",
    ]
    for tool, info in s.get("top_tools", {}).items():
        lines.append(f"- {tool}: {info['count']} 次（{info['pct']}%）")

    failures = s.get("failure_patterns", {})
    if failures:
        lines += ["", "### 失败模式", ""]
        for p, c in failures.items():
            lines.append(f"- {p}: {c} 次")

    loops = s.get("loop_tools", {})
    if loops:
        lines += ["", "### 循环工具", ""]
        for t, c in loops.items():
            lines.append(f"- {t}: {c} 次循环")

    sugg = report.get("suggestions", [])
    lines += ["", f"## 优化建议（{len(sugg)} 条）", ""]
    for i, sg in enumerate(sugg, 1):
        lines += [
            f"### {i}. [{sg.get('priority', 'LOW')}] {sg.get('section', '?')} → {sg.get('target', '?')}",
            "",
            f"- **现状**：{sg.get('current_behavior', '')}",
            f"- **建议**：{sg.get('suggested_change', '')}",
            f"- **理由**：{sg.get('reasoning', '')}",
            "",
        ]
    return "\n".join(lines)


def generate_report() -> dict[str, Any]:
    """运行分析并将报告写入 logs/，返回报告 dict。"""
    report = analyze_and_suggest()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = _LOGS_DIR / f"prompt_optimization_{ts}.json"
    md_path = _LOGS_DIR / f"prompt_optimization_{ts}.md"
    try:
        _LOGS_DIR.mkdir(exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(_render_markdown(report))
        report["report_files"] = {"json": str(json_path), "markdown": str(md_path)}
    except Exception as e:
        report["report_write_error"] = str(e)
    return report


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="CrawAgent 提示词优化分析（分析 logs/ 归档会话）")
    parser.add_argument("--no-llm", action="store_true", help="只跑统计层，不调用 LLM（快速查看数据概况）")
    args = parser.parse_args()

    if args.no_llm:
        archives = _load_archives()
        stats = compute_statistics(archives)
        print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
        sys.exit(0)

    rep = generate_report()
    print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    if rep.get("report_files"):
        md = rep["report_files"]["markdown"]
        print(f"\n[report] Markdown 已写入: {md}", file=sys.stderr)
    sys.exit(0 if rep.get("ok") else 1)
