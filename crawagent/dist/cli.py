"""Worker CLI 入口 — `crawagent-dist-worker` 命令行启动 worker 进程。

用法：
    uv run crawagent-dist-worker                     # 无限模式
    uv run crawagent-dist-worker --max-tasks 5       # 跑 5 个任务后退出
    uv run crawagent-dist-worker --once              # 只跑 1 个任务
    uv run crawagent-dist-worker --model glm-5.3     # 指定模型
"""
from __future__ import annotations

import argparse
import sys

from crawagent.dist.redis_server import ensure_redis
from crawagent.dist.worker import worker_main


def main():
    """CLI 主入口。"""
    parser = argparse.ArgumentParser(
        description="CrawAgent 分布式 worker 进程",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--max-tasks", type=int, default=0,
                        help="最多跑多少个任务后退出（0=无限）")
    parser.add_argument("--once", action="store_true",
                        help="只跑 1 个任务后退出（等同 --max-tasks 1）")
    parser.add_argument("--model", type=str, default="",
                        help="指定模型 ID（留空用默认）")
    parser.add_argument("--no-redis-check", action="store_true",
                        help="跳过 Redis 连接检查（已知 Redis 可用时加速启动）")

    args = parser.parse_args()

    max_tasks = 1 if args.once else args.max_tasks

    # 确保 Redis 可用
    if not args.no_redis_check:
        if not ensure_redis():
            print("[worker] Redis 不可用，无法启动 worker", file=sys.stderr)
            sys.exit(1)

    # 启动 worker 主循环
    worker_main(max_tasks=max_tasks)


if __name__ == "__main__":
    main()
