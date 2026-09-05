"""Phase 4.4 — CrawAgent CLI 入口。

三个子命令：
  start      启动 WebUI（uvicorn，端口 8006）
  doctor     环境健康检查（Python / .env / API key / data 目录 / 依赖版本）
  add-tool   从 _template/ 脚手架创建一个新的爬虫工具模块

用法：
  uv run crawagent start
  uv run crawagent doctor
  uv run crawagent add-tool my_new_crawler
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def _cmd_start(args: argparse.Namespace) -> int:
    """启动 WebUI。先检查 fastapi/uvicorn 是否已装（它们在 optional-deps [api]）。"""
    try:
        import uvicorn  # noqa: F401
        import fastapi  # noqa: F401
    except ImportError:
        print("❌ 缺少 api 依赖（fastapi / uvicorn）。请先装：")
        print("   uv sync --extra api")
        return 2

    from crawagent.web.server import main

    print("🚀 启动 CrawAgent WebUI (http://127.0.0.1:8006)")
    main()
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    """环境健康检查。"""
    checks: list[tuple[str, bool, str]] = []

    # Python 版本
    py_ok = sys.version_info >= (3, 11)
    checks.append(
        (
            "Python ≥ 3.11",
            py_ok,
            f"当前 {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        )
    )

    # 关键目录
    root = Path(__file__).resolve().parent.parent
    for label, rel in [
        ("crawagent/ 源码", "crawagent"),
        ("data/        数据", "data"),
        ("web/dist/    前端构建产物", "web/dist"),
    ]:
        p = root / rel
        checks.append((label, p.exists(), str(p)))

    # .env 文件
    env_path = root / ".env"
    checks.append((".env 配置文件", env_path.exists(), str(env_path)))

    # API key（只打 mask）
    try:
        from crawagent.config.settings import get_settings

        s = get_settings()
        api_key = getattr(s, "api_key", "") or ""
        key_ok = bool(api_key) and len(api_key) > 5
        masked = (api_key[:4] + "***" + api_key[-4:]) if api_key else "(未配置)"
        checks.append(
            (
                "API Key (DEEPSEEK_API_KEY)",
                key_ok,
                masked,
            )
        )
    except Exception as e:
        checks.append(("API Key", False, f"读取配置失败: {e}"))

    # 关键依赖版本
    for dep in ("langchain", "langgraph", "langchain_openai", "yaml"):
        try:
            mod = __import__(dep)
            ver = getattr(mod, "__version__", "ok")
            checks.append((f"依赖: {dep}", True, str(ver)))
        except ImportError:
            checks.append((f"依赖: {dep}", False, "未安装"))

    # 打印报告
    print("\n🔍 CrawAgent Doctor Report")
    print("=" * 60)
    ok_count = 0
    for label, ok, detail in checks:
        icon = "✅" if ok else "❌"
        if ok:
            ok_count += 1
        print(f"  {icon}  {label:<22}  {detail}")
    print("=" * 60)
    total = len(checks)
    print(f"  结果: {ok_count}/{total} 通过")
    print()

    return 0 if ok_count == total else 1


def _cmd_add_tool(args: argparse.Namespace) -> int:
    """从 tools/_template/ 复制脚手架创建新工具模块。"""
    name = args.name.strip()
    if not name or not name.replace("_", "").isalnum():
        print(f"❌ 非法工具名: '{name}'（只允许字母/数字/下划线）")
        return 2

    src_dir = Path(__file__).resolve().parent / "tools" / "_template"
    dst_dir = Path(__file__).resolve().parent / "tools" / name

    if not src_dir.exists():
        print(f"❌ 模板目录不存在: {src_dir}")
        return 2
    if dst_dir.exists():
        print(f"❌ 已存在同名工具目录: {dst_dir}")
        return 2

    shutil.copytree(src_dir, dst_dir)

    # 把 example_tool.py 重命名为 {name}_tool.py
    example = dst_dir / "example_tool.py"
    target = dst_dir / f"{name}_tool.py"
    if example.exists():
        example.rename(target)
        # 替换文件内容里的 "example" → name
        text = target.read_text(encoding="utf-8")
        text = text.replace("example", name)
        target.write_text(text, encoding="utf-8")

    # 更新 __init__.py 里的 import
    init = dst_dir / "__init__.py"
    if init.exists():
        text = init.read_text(encoding="utf-8")
        text = text.replace("example_tool", f"{name}_tool")
        text = text.replace("example", name)
        init.write_text(text, encoding="utf-8")

    print(f"✅ 已创建新工具: {dst_dir}")
    print(f"   模块文件: {target}")
    print(f"\n下一步:")
    print(f"  1. 在 {target} 里实现 build_tool()")
    print(f"  2. 写个简单测试: tests/test_{name}.py")
    print(f"  3. 自动发现机制会在下一次 import crawagent.tools 时收录它")
    return 0


def main() -> int:
    # Windows 默认 GBK 编码无法输出 emoji，强制使用 UTF-8
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        prog="crawagent",
        description="CrawAgent CLI — 启动、诊断、插件脚手架",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("start", help="启动 WebUI (uvicorn, 端口 8006)")

    p_d = sub.add_parser("doctor", help="环境健康检查")

    p_a = sub.add_parser("add-tool", help="从脚手架创建新爬虫工具")
    p_a.add_argument("name", help="工具名（如 douban_reader）")

    args = parser.parse_args()

    if args.command == "start":
        return _cmd_start(args)
    elif args.command == "doctor":
        return _cmd_doctor(args)
    elif args.command == "add-tool":
        return _cmd_add_tool(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
