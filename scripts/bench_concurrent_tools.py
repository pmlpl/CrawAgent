"""并发 IO 工具冒烟基准 — 验证 LangChain 默认 offload vs @offload 装饰器

跑两个场景，对比 BEFORE vs AFTER：
1. BEFORE：`@tool` 直接装饰 sync 函数（LangChain 默认 offload）
2. AFTER：`@tool` + `@offload`（来自 _async_compat，显式标记 + asyncio.to_thread）

测两个指标：
- N 并发 sleep S 秒：总耗时（并行应 < 1.5×S；串行 ~N×S）
- 心跳最大间隔（应 < 0.5s；阻塞会 >1s）

用法：
    python scripts/bench_concurrent_tools.py --mode before
    python scripts/bench_concurrent_tools.py --mode after

退出码：0=PASS/WARN，1=FAIL
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
import threading

from langchain_core.tools import tool


def _build_slow_tool(use_offload: bool):
    """构造测试工具 — 根据 use_offload 决定是否走 @offload"""
    if use_offload:
        from crawagent.tools._async_compat import offload

        @tool
        @offload
        def slow_tool(seconds: int) -> str:
            """模拟 IO 密集工具：sleep N 秒，返回线程 id 与耗时"""
            start = time.monotonic()
            tid = threading.get_ident()
            time.sleep(seconds)
            return f"thread={tid}, slept={time.monotonic()-start:.2f}s"

        return slow_tool
    else:
        @tool
        def slow_tool(seconds: int) -> str:
            """模拟 IO 密集工具：sleep N 秒，返回线程 id 与耗时"""
            start = time.monotonic()
            tid = threading.get_ident()
            time.sleep(seconds)
            return f"thread={tid}, slept={time.monotonic()-start:.2f}s"

        return slow_tool


# ---------------------------------------------------------------------------
# 基准测试
# ---------------------------------------------------------------------------

async def run_bench(slow_tool, concurrency: int, sleep_seconds: int) -> dict:
    """跑一轮基准，返回耗时 + 心跳间隔"""
    heartbeat_intervals: list[float] = []
    last_beat = time.monotonic()

    async def heartbeat():
        nonlocal last_beat
        while True:
            await asyncio.sleep(0.1)
            now = time.monotonic()
            heartbeat_intervals.append(now - last_beat)
            last_beat = now

    hb_task = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.05)  # 让心跳跑起来

    tasks = [
        slow_tool.ainvoke({"seconds": sleep_seconds})
        for _ in range(concurrency)
    ]
    start = time.monotonic()
    results = await asyncio.gather(*tasks)
    elapsed = time.monotonic() - start

    await asyncio.sleep(0.5)
    hb_task.cancel()
    try:
        await hb_task
    except asyncio.CancelledError:
        pass

    threads = {r.split(",")[0] for r in results}
    max_hb = max(heartbeat_intervals) if heartbeat_intervals else 0
    avg_hb = sum(heartbeat_intervals) / len(heartbeat_intervals) if heartbeat_intervals else 0

    return {
        "concurrency": concurrency,
        "sleep_per_call": sleep_seconds,
        "total_elapsed_s": elapsed,
        "heartbeat_max_interval_s": max_hb,
        "heartbeat_avg_interval_s": avg_hb,
        "unique_threads": len(threads),
        "thread_ids": sorted(threads),
    }


def render_verdict(stats: dict, sleep_seconds: int) -> tuple[str, str]:
    """判据：
    - 总耗时 < 1.5 × sleep_seconds → 并行 ✓
    - 心跳最大间隔 < 0.5s → 事件循环未阻塞 ✓
    """
    elapsed = stats["total_elapsed_s"]
    max_hb = stats["heartbeat_max_interval_s"]
    conc = stats["concurrency"]
    parallel_expected = sleep_seconds * 1.5
    if elapsed > parallel_expected:
        return (
            "FAIL",
            f"{conc}×{sleep_seconds}s 调用总耗时 {elapsed:.2f}s（>{parallel_expected:.1f}s）"
            f"→ 串行执行，线程池已饱和",
        )
    if max_hb > 0.5:
        return (
            "FAIL",
            f"心跳最大间隔 {max_hb:.3f}s（>0.5s）→ 事件循环被阻塞",
        )
    if stats["unique_threads"] < conc:
        return (
            "WARN",
            f"并发 {conc} 个调用只用了 {stats['unique_threads']} 个线程"
            f"→ 线程复用但执行确实并行（OK）",
        )
    return (
        "PASS",
        f"{conc}×{sleep_seconds}s 并行总耗时 {elapsed:.2f}s"
        f"={stats['unique_threads']} 个独立线程"
        f"，心跳间隔 {max_hb:.3f}s",
    )


def main():
    parser = argparse.ArgumentParser(description="并发 IO 工具冒烟基准")
    parser.add_argument("--mode", choices=["before", "after"], default="before",
                        help="before=纯 @tool（LangChain 默认 offload），after=@tool + @offload")
    parser.add_argument("--concurrency", type=int, default=4, help="并发数（默认 4）")
    parser.add_argument("--sleep", type=float, default=2.0, help="每次工具 sleep 秒数（默认 2）")
    args = parser.parse_args()

    label = "AFTER (@offload)" if args.mode == "after" else "BEFORE (default)"
    print(f"=== CrawAgent IO offload bench — {label} ===")
    print(f"并发: {args.concurrency}  |  sleep: {args.sleep}s")

    slow_tool = _build_slow_tool(use_offload=(args.mode == "after"))
    stats = asyncio.run(run_bench(slow_tool, args.concurrency, args.sleep))
    verdict, msg = render_verdict(stats, args.sleep)

    print()
    print("结果：")
    for k, v in stats.items():
        if k != "thread_ids":
            print(f"  {k}: {v}")
    print(f"  thread_ids: {stats['thread_ids']}")
    print()
    print(f"[{verdict}] {msg}")
    print()

    sys.exit(0 if verdict in ("PASS", "WARN") else 1)


if __name__ == "__main__":
    main()