"""Phase 4.4 — CrawAgent CLI 入口。

子命令：
  start       启动 WebUI（uvicorn，端口 8006）
  doctor      环境健康检查（Python / .env / API key / data 目录 / 依赖版本）
  add-tool    从 _template/ 脚手架创建一个新的插件包（plugins/<name>/）
  add-plugin  从本地路径或 git URL 一键接入第三方插件
  plugins     列出已安装插件

用法：
  uv run crawagent start
  uv run crawagent doctor
  uv run crawagent add-tool my_new_crawler
  uv run crawagent add-plugin D:/path/to/some-plugin
  uv run crawagent add-plugin https://github.com/user/some-plugin.git
  uv run crawagent plugins
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


def _plugins_root() -> Path:
    """第三方插件根目录：项目根/plugins/。"""
    return Path(__file__).resolve().parent.parent / "plugins"


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
    """从 tools/_template/ 脚手架创建一个插件包（plugins/<name>/，P2-9 插件规范）。"""
    name = args.name.strip()
    if not name or not name.replace("_", "").isalnum():
        print(f"❌ 非法插件名: '{name}'（只允许字母/数字/下划线）")
        return 2

    src_dir = Path(__file__).resolve().parent / "tools" / "_template"
    plugins = _plugins_root()
    dst_dir = plugins / name

    if not src_dir.exists():
        print(f"❌ 模板目录不存在: {src_dir}")
        return 2
    if dst_dir.exists():
        print(f"❌ 已存在同名插件目录: {dst_dir}")
        return 2

    plugins.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src_dir, dst_dir)

    # tools/example_tool.py → tools/<name>_tool.py，并替换内容里的示例名
    tools_dir = dst_dir / "tools"
    example = tools_dir / "example_tool.py"
    if example.exists():
        target = tools_dir / f"{name}_tool.py"
        example.rename(target)
        text = target.read_text(encoding="utf-8")
        text = text.replace("example_tool", f"{name}_tool").replace("example-plugin", name)
        target.write_text(text, encoding="utf-8")

    # skills/example-usage → skills/<name>-usage，SKILL.md 的 name 同步改
    skills_dir = dst_dir / "skills"
    example_skill = skills_dir / "example-usage"
    if example_skill.is_dir():
        skill_dst = skills_dir / f"{name}-usage"
        example_skill.rename(skill_dst)
        sk = skill_dst / "SKILL.md"
        if sk.exists():
            text = sk.read_text(encoding="utf-8")
            text = text.replace("name: example-usage", f"name: {name}-usage")
            sk.write_text(text, encoding="utf-8")

    # manifest：name 落成真实插件名
    manifest_path = dst_dir / "plugin.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["name"] = name
            manifest["description"] = f"{name} 插件（add-tool 脚手架生成，待实现）"
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except (json.JSONDecodeError, OSError):
            pass

    print(f"✅ 已创建插件: {dst_dir}")
    print(f"   工具模块: {tools_dir / f'{name}_tool.py'}")
    print("\n下一步:")
    print(f"  1. 在 tools/{name}_tool.py 里实现你的工具（@tool / @register_tool）")
    print(f"  2. 按需在 skills/{name}-usage/SKILL.md 写站点攻略")
    print(f"  3. 重启后端 — 插件工具自动进 Agent 工具表，技能自动进索引")
    print(f"  4. uv run crawagent plugins 可查看插件加载情况")
    return 0


def _cmd_add_plugin(args: argparse.Namespace) -> int:
    """从本地路径或 git URL 接入第三方插件到 plugins/。"""
    source = args.source.strip().rstrip("/\\")
    if not source:
        print("❌ 请给插件来源：本地目录路径或 git 仓库 URL")
        return 2

    plugins = _plugins_root()

    # 1. 拿到源目录（git URL 就地 clone 到临时目录）
    if re.match(r"^https?://|\.git$", source) or source.startswith("git@"):
        tmp = plugins / "_tmp_clone"
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        plugins.mkdir(parents=True, exist_ok=True)
        print(f"⏳ 克隆 {source} ...")
        r = subprocess.run(["git", "clone", "--depth", "1", source, str(tmp)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f"❌ git clone 失败:\n{r.stderr[:500]}")
            shutil.rmtree(tmp, ignore_errors=True)
            return 2
        src_dir = tmp
    else:
        src_dir = Path(source).expanduser().resolve()
        if not src_dir.is_dir():
            print(f"❌ 本地目录不存在: {src_dir}")
            return 2

    # 2. 校验 manifest，确定插件目录名
    manifest_path = src_dir / "plugin.json"
    if not manifest_path.exists():
        print(f"❌ 不是合法的 CrawAgent 插件：缺 plugin.json（{src_dir}）")
        if src_dir.name == "_tmp_clone":
            shutil.rmtree(src_dir, ignore_errors=True)
        return 2
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"❌ plugin.json 解析失败: {e}")
        if src_dir.name == "_tmp_clone":
            shutil.rmtree(src_dir, ignore_errors=True)
        return 2

    plugin_name = re.sub(r"[^0-9A-Za-z_-]", "_", str(manifest.get("name") or Path(source).stem))
    dst_dir = plugins / plugin_name
    if dst_dir.exists():
        print(f"❌ 已存在同名插件: {dst_dir}（先删除或改名再装）")
        if src_dir.name == "_tmp_clone":
            shutil.rmtree(src_dir, ignore_errors=True)
        return 2

    # 3. 安装
    plugins.mkdir(parents=True, exist_ok=True)
    if src_dir.name == "_tmp_clone":
        src_dir.rename(dst_dir)
    else:
        shutil.copytree(src_dir, dst_dir)

    print(f"✅ 插件已安装: {dst_dir}")
    deps = manifest.get("dependencies", [])
    req = dst_dir / "requirements.txt"
    if req.exists():
        print(f"   ⚠ 该插件声明了 requirements.txt，请手动安装: uv pip install -r {req}")
    elif deps:
        print(f"   ⚠ 声明依赖: {', '.join(map(str, deps))}（缺什么装什么: uv pip install ...）")
    print("   重启后端生效。uv run crawagent plugins 查看状态。")
    return 0


def _cmd_plugins(args: argparse.Namespace) -> int:
    """列出已安装插件与各自的工具/技能数量。"""
    from crawagent.tools.registry import list_plugins

    plugins = list_plugins()
    print("\n🧩 CrawAgent 插件")
    print("=" * 60)
    if not plugins:
        print("  （plugins/ 目录为空。用 crawagent add-tool <name> 创建，")
        print("    或 crawagent add-plugin <路径|git URL> 接入第三方插件）")
        print("=" * 60)
        return 0
    valid = 0
    for p in plugins:
        if not p["valid"]:
            print(f"  ❌ {p['name']:<24} {p['error']}  [{p['dir']}]")
            continue
        valid += 1
        tdir = Path(p["dir"]) / "tools"
        sdir = Path(p["dir"]) / "skills"
        tools = [t for t in sorted(tdir.glob("*.py")) if not t.stem.startswith("_")] if tdir.is_dir() else []
        skills = [s for s in sorted(sdir.iterdir()) if s.is_dir()] if sdir.is_dir() else []
        ver = f" v{p['version']}" if p["version"] else ""
        deps = f" deps={','.join(p['dependencies'])}" if p["dependencies"] else ""
        print(f"  ✅ {p['name']:<24}{ver}  tools={len(tools)} skills={len(skills)}{deps}")
        if p["description"]:
            print(f"      {p['description'][:80]}")
    print("=" * 60)
    print(f"  共 {len(plugins)} 个插件，{valid} 个有效。重启后端生效。")
    return 0


def main() -> int:
    """CLI 入口（``python -m crawagent`` / ``crawagent`` 命令）。

    支持子命令：
    - ``start`` — 启动 WebUI 后端（默认行为）
    - ``plugin <subcmd>`` — 插件管理（list / enable / disable / info / path）
    - ``cron <subcmd>`` — 定时任务管理（list / run / enable / disable）

    Returns:
        退出码（0 成功 / 非 0 失败）。
    """
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

    p_a = sub.add_parser("add-tool", help="从脚手架创建新插件包（plugins/<name>/）")
    p_a.add_argument("name", help="插件名（如 douban_reader）")

    p_ap = sub.add_parser("add-plugin", help="从本地路径或 git URL 一键接入第三方插件")
    p_ap.add_argument("source", help="插件来源：本地目录路径或 git 仓库 URL")

    sub.add_parser("plugins", help="列出已安装插件")

    args = parser.parse_args()

    if args.command == "start":
        return _cmd_start(args)
    elif args.command == "doctor":
        return _cmd_doctor(args)
    elif args.command == "add-tool":
        return _cmd_add_tool(args)
    elif args.command == "add-plugin":
        return _cmd_add_plugin(args)
    elif args.command == "plugins":
        return _cmd_plugins(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
