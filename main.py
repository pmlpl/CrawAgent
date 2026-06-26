"""CrawAgent 主入口

用法:
    python main.py                  # 启动交互终端 UI
    python main.py --check-deps     # 检查依赖是否齐全
"""
from __future__ import annotations

import argparse
import sys

# 加载 .env 环境变量（如果存在）
try:
    from dotenv import load_dotenv
    load_dotenv()  # 自动加载项目根目录的 .env 文件
except ImportError:
    pass  # 如果没有安装 python-dotenv，跳过加载


def _check_dependencies() -> bool:
    """检查核心依赖是否安装。返回 True 表示全部就绪。"""
    required = [
        ("langchain", "langchain 核心"),
        ("langgraph", "LangGraph 工作流"),
        ("httpx", "HTTP 客户端"),
        ("bs4", "BeautifulSoup 解析 HTML"),
    ]
    optional = [
        ("rich", "终端美化"),
        ("prompt_toolkit", "更好的输入体验"),
        ("lxml", "更快的 HTML 解析"),
    ]

    all_ok = True
    print("=== 核心依赖 ===")
    for pkg, desc in required:
        try:
            __import__(pkg)
            print(f"  ✅ {pkg:<20} {desc}")
        except ImportError:
            print(f"  ❌ {pkg:<20} {desc}  (请: pip install {pkg})")
            all_ok = False

    print("\n=== 可选依赖 ===")
    for pkg, desc in optional:
        try:
            __import__(pkg)
            print(f"  ✅ {pkg:<20} {desc}")
        except ImportError:
            print(f"  ⚪ {pkg:<20} {desc}  (未安装，功能降级)")

    return all_ok


def main() -> None:
    parser = argparse.ArgumentParser(description="CrawAgent - 智能爬虫 Agent")
    parser.add_argument(
        "--check-deps", action="store_true",
        help="只检查依赖是否齐全，不启动终端",
    )
    args = parser.parse_args()

    if args.check_deps:
        ok = _check_dependencies()
        sys.exit(0 if ok else 1)

    # === 启动终端 UI ===
    try:
        from crawagent.ui import TerminalUI
    except ImportError as e:
        print(f"[错误] 导入模块失败: {e}")
        print("请先安装依赖: pip install -r requirements.txt")
        sys.exit(1)

    TerminalUI().run()


if __name__ == "__main__":
    main()
