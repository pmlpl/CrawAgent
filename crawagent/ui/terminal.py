"""终端 UI - CrawAgent

- 自然语言对话：直接打字
- / 命令：/add_model /use_model /rm_model /models /skills /export /debug /browser /help /clear /exit
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Callable

from ..config.settings import ModelConfig, Settings, load_settings
from ..graph.workflow import CrawState, CrawWorkflow
from ..graph.agent_workflow import AgentWorkflow, AgentState
from ..llm.factory import LLMFactory
from ..tools import export_csv, export_json, export_markdown, extract_urls
from ..agent.skill_loader import load_all_skills, Skill


# ============================================================
# 对话上下文自动补全
# ============================================================

def _enrich_user_input(user_input: str, history: list[dict]) -> str:
    """如果用户输入没有 URL，从历史中补全上下文"""
    from ..tools import extract_urls

    urls = extract_urls(user_input)
    if urls:
        return user_input  # 已有 URL，无需补全

    # 从历史中找最新一轮的 URL 和任务描述
    last = history[-1] if history else None
    if not last or not last.get("extracted_urls"):
        return user_input

    last_urls = last.get("extracted_urls", [])
    last_task = last.get("user_input", "")

    # 补全上下文
    url_list = "\n".join(f"- {u}" for u in last_urls)
    enriched = (
        f"继续上一轮任务：{last_task}\n"
        f"已获取的 URL：\n{url_list}\n"
        f"用户新请求：{user_input}"
    )
    return enriched


# ============================================================
# 简单对话检测
# ============================================================

def _is_simple_chat(user_input: str) -> bool:
    """判断用户输入是否为简单对话（不需要 Agent 爬取流程）"""
    # 有 URL → 不是简单对话
    if extract_urls(user_input):
        return False
    # 有爬取相关关键词 → 不是简单对话
    task_keywords = (
        "爬", "抓", "下载", "获取", "抓取", "爬取",
        "scrape", "crawl", "download", "抓包", "抓视频",
        "壁纸", "wallpaper", "image", "img", "图片",
        "视频", "video", "movie", "movie",
    )
    if any(kw in user_input.lower() for kw in task_keywords):
        return False
    # 看视频相关 → 不是简单对话（走 Agent 工作流，调用 watch_video）
    watch_video_keywords = ("想看", "播放", "看一下", "打开", "帮我找", "有没有", "搜一下")
    if any(kw in user_input for kw in watch_video_keywords):
        # 进一步判断：如果有书名号或"电视剧/电影/综艺/动漫"等后缀，肯定是看视频
        import re
        if re.search(r'[《》]|电视剧|电影|综艺|动漫|动画片|剧', user_input):
            return False
        # 有"想看"+ 2字以上的名称，也算
        for kw in watch_video_keywords:
            if kw in user_input:
                idx = user_input.find(kw) + len(kw)
                name = user_input[idx:].strip('《》"\' ，。！？')
                if len(name) >= 2:
                    return False
    # 单独的 "看XXX电影/电视剧/综艺..." 模式
    import re
    if re.search(r'^看\s*.+(?:电视剧|电影|综艺|动漫|动画片|剧)$', user_input.strip()):
        return False
    # 纯对话/问候/闲聊 → 是简单对话
    chat_keywords = (
        "你好", "您好", "hi", "hello", "嗨", "hey",
        "谢谢", "thanks", "再见", "bye",
        "怎么", "如何", "是什么", "为什么",
        "帮我", "请", "能不能",
    )
    # 注意："帮我" 等需要进一步判断——如果后面跟的是视频名就不是简单对话
    if any(kw in user_input.lower() for kw in chat_keywords):
        # 但如果有"帮我"+ 视频相关词，不算简单对话
        if "帮我" in user_input and any(vk in user_input for vk in ("看", "播放", "找", "搜")):
            return False
        return True
    # 其他情况，超过 20 个字也当任务处理
    if len(user_input.strip()) > 20:
        return False
    return True


# ============================================================
# 富文本打印 - 若 rich 未装则降级
# ============================================================

class ConsolePrinter:

    def __init__(self):
        try:
            from rich.console import Console
            from rich.panel import Panel
            from rich.text import Text
            self._rich_console = Console()
            self._Panel = Panel
            self._Text = Text
            self.has_rich = True
        except ImportError:
            self.has_rich = False
            self._rich_console = None
            self._Panel = None
            self._Text = None

    def banner(self) -> None:
        title = "🕷️  CrawAgent - 你的智能爬虫助理"
        sub = "基础爬取 + LLM 摘要  |  输入 /help 查看命令"
        if self.has_rich and self._Panel is not None:
            self._rich_console.print(self._Panel.fit(title + "\n" + sub, border_style="cyan"))
        else:
            print("=" * 60)
            print(f"  {title}")
            print(f"  {sub}")
            print("=" * 60)

    def info(self, msg: str) -> None:
        if self.has_rich:
            self._rich_console.print(f"[cyan][系统][/cyan] {msg}")
        else:
            print(f"[系统] {msg}")

    def thinking(self, msg: str = "思考中...") -> None:
        if self.has_rich:
            self._rich_console.print(f"[yellow]  ⏳ {msg}[/yellow]")
        else:
            print(f"  ⏳ {msg}")

    def reply(self, text: str) -> None:
        if self.has_rich and self._Panel is not None:
            self._rich_console.print(self._Panel(text, title="CrawAgent", border_style="green"))
        else:
            print("-" * 50)
            print(f"CrawAgent: {text}")
            print("-" * 50)

    def error(self, msg: str) -> None:
        if self.has_rich:
            self._rich_console.print(f"[red][错误][/red] {msg}")
        else:
            print(f"[错误] {msg}")

    def list_box(self, title: str, rows: list[tuple[str, str, str]]) -> None:
        lines: list[str] = []
        for r in rows:
            line = f"  {r[0]:<20} {r[1]}"
            if len(r) > 2 and r[2]:
                line += f"   [{r[2]}]"
            lines.append(line)
        body = "\n".join(lines)
        if self.has_rich and self._Panel is not None:
            self._rich_console.print(self._Panel(body, title=title, border_style="blue"))
        else:
            print(f"-- {title} --")
            print(body)

    def blank(self) -> None:
        print()


# ============================================================
# 交互式模型添加
# ============================================================

def _prompt(msg: str, default: str = "") -> str:
    """返回用户输入，如果 Ctrl+C 则抛出 Cancelled 异常"""
    try:
        if default:
            raw = input(f"{msg} (回车用默认值 '{default}'): ").strip()
            return raw or default
        raw = input(f"{msg}: ").strip()
        return raw
    except (KeyboardInterrupt, EOFError):
        raise CancelledError("用户取消输入")


class CancelledError(Exception):
    """用户主动取消操作"""
    pass


def _select_command_menu(commands: dict[str, CommandHandler]) -> str:
    """
    显示交互式命令选择菜单
    - 上下键: 移动选择
    - 回车: 确认选择
    - Ctrl+C / Esc: 取消

    返回选中的命令字符串（如 "/models"），空字符串表示取消
    """
    cmd_list = sorted(commands.keys())
    if not cmd_list:
        return ""

    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.shortcuts import CompleteStyle
        from prompt_toolkit.completion import Completer, Completion

        # 命令描述（简化版）
        descriptions: dict[str, str] = {
            "/help": "显示帮助",
            "/models": "列出所有模型",
            "/use_model": "切换当前模型",
            "/add_model": "交互式添加模型",
            "/rm_model": "删除模型",
            "/skills": "查看已加载的 Skill 列表",
            "/skill_new": "创建新 Skill（骨架文件）",
            "/export": "导出最近一次爬取结果",
            "/debug": "切换有头调试模式",
            "/browser": "浏览器模式控制",
            "/agent": "切换智能 Agent 模式",
            "/wallpaper": "壁纸爬虫配置",
            "/video": "视频爬虫配置",
            "/clear": "清屏",
            "/exit": "退出程序",
        }

        # 创建一个自定义的 completer
        class CommandCompleter(Completer):
            def get_completions(self, document, complete_event):
                text = document.text
                for cmd in cmd_list:
                    if cmd.startswith(text) or text in cmd:
                        desc = descriptions.get(cmd, "")
                        yield Completion(
                            cmd,
                            start_position=-len(text),
                            display=cmd,
                            display_meta=desc,
                        )

        bindings = KeyBindings()

        @bindings.add('c-c')
        @bindings.add('escape')
        def _cancel(event):
            event.app.exit(result="")

        session = PromptSession(
            completer=CommandCompleter(),
            complete_style=CompleteStyle.MULTI_COLUMN,
            key_bindings=bindings,
        )

        print("\n  ⚡  命令选择器（上下键↑↓ 选择，Tab/回车 补全确认，Esc 取消）:")
        print("  " + "─" * 58)

        # 先显示所有命令的简要列表，让用户知道有什么
        for i, cmd in enumerate(cmd_list, 1):
            desc = descriptions.get(cmd, "")
            indicator = "   " if i > 10 else f"{i:2}."
            print(f"  {indicator} {cmd:<12} {desc}")

        print("  " + "─" * 58)

        try:
            # 让用户输入命令，prompt_toolkit 会自动补全
            result = session.prompt("  输入命令（或输入前几个字符 Tab 补全）: ",
                                     default="/",
                                     complete_while_typing=True)
            result = result.strip()
            return result if result.startswith("/") else ""
        except (EOFError, KeyboardInterrupt):
            return ""

    except ImportError:
        # 降级：简单的数字选择
        print("\n  可用命令列表（输入编号或命令名选择）:")
        for i, cmd in enumerate(cmd_list, 1):
            print(f"  {i:2}. {cmd}")

        try:
            raw = input("  请选择（输入编号或命令名，回车取消）: ").strip()
        except (KeyboardInterrupt, EOFError):
            return ""

        if not raw:
            return ""

        # 如果输入的是数字
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(cmd_list):
                return cmd_list[idx]

        # 如果输入的命令存在
        if raw in cmd_list:
            return raw

        # 模糊匹配
        for cmd in cmd_list:
            if raw in cmd:
                return cmd

        return ""


def _ask_new_model_interactive(models_file: Path) -> tuple[bool, str]:
    """交互式添加一个模型 —— 返回 (成功, 消息)"""
    print()
    print("  📝 添加模型 —— 按下面提示输入（Ctrl+C 取消）")
    print("  provider 只有两种: openai    （OpenAI / DeepSeek / LM Studio / Ollama 等兼容 API）")
    print("                    anthropic  （Anthropic Claude 系列）")
    print()

    try:
        name = _prompt("  1/6 模型别名（随便起，简单好记）", default="my-model")
        provider = _prompt("  2/6 provider（openai / anthropic）", default="openai").lower()
        if provider not in ("openai", "anthropic"):
            return False, f"provider 只能是 openai 或 anthropic，收到: {provider}"

        if provider == "openai":
            base_url_default = "https://api.deepseek.com/v1"
        else:
            base_url_default = "https://api.anthropic.com/v1"

        base_url = _prompt("  3/6 base_url", default=base_url_default)
        model_name = _prompt("  4/6 model_name（具体模型名，如 deepseek-chat / qwen2.5:7b）", default="deepseek-chat")
        api_key = _prompt("  5/6 api_key（本地服务可留空）", default="")
        try:
            temp = float(_prompt("  6/6 temperature (0~1)", default="0.7"))
        except ValueError:
            return False, "temperature 必须是数字"

        description = _prompt("  备注/描述（可选）", default="")

        cfg = ModelConfig(
            name=name,
            provider=provider,
            base_url=base_url,
            model_name=model_name,
            api_key=api_key,
            temperature=temp,
            max_tokens=2000,
            description=description,
        )

        # 临时加载 settings 来保存
        settings = load_settings(models_file)
        ok, msg = settings.add_model(cfg)
        return ok, msg

    except CancelledError:
        print("\n  已取消，返回主菜单")
        return False, ""


# ============================================================
# 终端 UI 主类
# ============================================================

CommandHandler = Callable[[str, "TerminalUI"], tuple[bool, str | None]]


class TerminalUI:

    def __init__(self):
        try:
            self.settings: Settings = load_settings()
        except Exception as e:
            print(f"[错误] 加载配置失败: {e}")
            sys.exit(1)

        self.llm_factory = LLMFactory(self.settings)
        # 旧版工作流（向后兼容）
        self.workflow = CrawWorkflow(self.settings, self.llm_factory)
        # 新版 Agent 工作流（真正的 Agent）
        self.agent_workflow = AgentWorkflow(self.settings, self.llm_factory)
        self.printer = ConsolePrinter()
        self._last_state: CrawState | None = None
        self._last_agent_state: AgentState | None = None
        
        # 对话历史（用于跨轮次上下文记忆）
        self._conversation_history: list[dict] = []  # [{"user_input": str, "reply": str, "extracted_urls": list, "tool_result": str}]

        # 默认使用新版 Agent 工作流
        self._use_agent_mode: bool = True

        # 浏览器模式状态
        self._debug_mode: bool = False
        self._force_browser: bool | None = None

        # 壁纸爬虫配置
        self._max_wallpapers: int = 12
        self._image_output_dir: str = "output/img"

        # 视频爬虫配置
        self._video_output_dir: str = "output/video"

        # 命令注册
        self._commands: dict[str, CommandHandler] = {
            "/help": self._cmd_help,
            "/models": self._cmd_models,
            "/use_model": self._cmd_use_model,
            "/add_model": self._cmd_add_model,
            "/rm_model": self._cmd_rm_model,
            "/skills": self._cmd_skills,
            "/skill_new": self._cmd_skill_new,
            "/export": self._cmd_export,
            "/debug": self._cmd_debug,
            "/browser": self._cmd_browser,
            "/wallpaper": self._cmd_wallpaper,
            "/video": self._cmd_video,
            "/agent": self._cmd_agent,  # 新增：切换 Agent 模式
            "/clear": self._cmd_clear,
            "/exit": self._cmd_exit,
            "/quit": self._cmd_exit,
        }

    # ---- 主循环 ----

    def run(self) -> None:
        self.printer.banner()
        self._print_model_info()
        self._print_browser_mode()
        self._print_agent_mode()
        self.printer.blank()
        self.printer.info("直接输入自然语言对话，或输入 / 打开命令选择器。/exit 退出。")
        self.printer.blank()

        while True:
            try:
                user_input = self._get_input().strip()
            except (EOFError, KeyboardInterrupt):
                self.printer.blank()
                self.printer.info("再见！👋")
                break

            if not user_input:
                continue

            # 用户输入了 "/" → 弹出命令选择器
            if user_input == "/":
                selected = _select_command_menu(self._commands)
                if selected:
                    self._handle_command(selected)
                continue

            if user_input.startswith("/"):
                self._handle_command(user_input)
                continue

            # 自动补全上下文（如果用户没给 URL 但历史有）
            if self._use_agent_mode and not user_input.startswith("/"):
                enriched = _enrich_user_input(user_input, self._conversation_history)
                if enriched != user_input:
                    print(f"  [记忆] 检测到历史上下文，已自动补充 URL")
                user_input = enriched

            self.printer.thinking()
            try:
                # 根据模式选择工作流
                if self._use_agent_mode:
                    # 简单对话 → 直接 LLM 回复，不走完整 Agent 流程
                    if _is_simple_chat(user_input):
                        llm = self.llm_factory.get_default()
                        from langchain_core.messages import HumanMessage, SystemMessage
                        response = llm.invoke([
                            SystemMessage(content="你是一个友好的爬虫助手，用简洁的语言回答用户的问题。如果用户只是问候或闲聊，直接友好回复，不需要提及爬虫功能。"),
                            HumanMessage(content=user_input),
                        ])
                        reply = str(response.content)
                        self.printer.reply(reply)
                    else:
                        # 完整 Agent 工作流（URL 任务）
                        state = self.agent_workflow.run(
                            user_input,
                            debug_mode=self._debug_mode,
                            force_browser=self._force_browser,
                            max_wallpapers=self._max_wallpapers,
                            image_output_dir=self._image_output_dir,
                            video_output_dir=self._video_output_dir,
                            max_iterations=3,
                        )
                        self._last_agent_state = state

                        # 保存到对话历史
                        self._conversation_history.append({
                            "user_input": user_input,
                            "reply": state["final_reply"],
                            "extracted_urls": state.get("extracted_urls", []),
                            "tool_result": state.get("tool_result", ""),
                        })
                        # 最多保留 10 轮
                        if len(self._conversation_history) > 10:
                            self._conversation_history = self._conversation_history[-10:]

                        self.printer.reply(state["final_reply"])
                else:
                    # 使用旧版工作流
                    state = self.workflow.run(
                        user_input,
                        debug_mode=self._debug_mode,
                        force_browser=self._force_browser,
                        max_wallpapers=self._max_wallpapers,
                        image_output_dir=self._image_output_dir,
                        video_output_dir=self._video_output_dir,
                    )
                    self._last_state = state
                    self.printer.reply(state.final_reply)
            except Exception as e:
                self.printer.error(f"执行出错: {e}")

            self.printer.blank()

    # ---- 输入 ----

    def _get_input(self) -> str:
        """
        智能输入框:
        - 正常输入: 自然语言对话
        - 输入 "/": 自动弹出命令候选下拉框（上下键选择，Tab/回车补全确认）
        """
        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.completion import Completer, Completion
            from prompt_toolkit.key_binding import KeyBindings

            # 命令描述映射
            cmd_descriptions: dict[str, str] = {
                "/help": "显示帮助",
                "/models": "列出所有模型",
                "/use_model": "切换当前模型",
                "/add_model": "交互式添加模型",
                "/rm_model": "删除模型",
                "/skills": "查看爬虫技能矩阵",
                "/export": "导出最近一次爬取结果",
                "/debug": "切换有头调试模式",
                "/browser": "浏览器模式控制",
                "/agent": "切换智能 Agent 模式",
                "/wallpaper": "壁纸爬虫配置",
                "/clear": "清屏",
                "/exit": "退出程序",
            }

            cmd_list = sorted(self._commands.keys())

            # 智能补全器: 输入 "/" 触发命令候选
            class SmartCompleter(Completer):
                def get_completions(self, document, complete_event):
                    text = document.text

                    # 情况1: 只输入了 "/" 或以 "/" 开头 —— 显示所有命令
                    if text == "/" or text.startswith("/"):
                        # 过滤匹配的命令
                        query = text[1:].lower()  # 去掉 "/" 后的部分
                        for cmd in cmd_list:
                            cmd_body = cmd[1:]  # 去掉 "/" 方便匹配
                            if query in cmd_body or cmd_body.startswith(query):
                                desc = cmd_descriptions.get(cmd, "")
                                yield Completion(
                                    cmd,
                                    start_position=-len(text),
                                    display=cmd,
                                    display_meta=desc,
                                )

            bindings = KeyBindings()

            # Tab 键: 触发补全
            @bindings.add('tab')
            def _complete(event):
                buffer = event.app.current_buffer
                if buffer.complete_state:
                    buffer.complete_next()
                else:
                    buffer.start_completion(select_first=True)

            # 上下方向键: 导航候选（只在有补全状态时）
            @bindings.add('down')
            def _down(event):
                buffer = event.app.current_buffer
                if buffer.complete_state:
                    buffer.complete_next()
                else:
                    buffer.cursor_down()

            @bindings.add('up')
            def _up(event):
                buffer = event.app.current_buffer
                if buffer.complete_state:
                    buffer.complete_previous()
                else:
                    buffer.cursor_up()

            session = PromptSession(
                completer=SmartCompleter(),
                key_bindings=bindings,
                complete_while_typing=True,
            )

            result = session.prompt("  > ")
            return result.strip()

        except ImportError:
            # 降级: 普通 input
            return input("  > ").strip()

    # ---- 内部帮助 ----

    def _print_model_info(self) -> None:
        name = self.llm_factory.current_name()
        cfg = self.settings.get_model(name) if name else None
        if not cfg:
            self.printer.info("当前无模型。运行 /add_model 添加。")
            return
        self.printer.info(f"当前模型: {cfg.display()}")

    def _print_browser_mode(self) -> None:
        """显示当前浏览器模式"""
        mode_parts = []
        if self._debug_mode:
            mode_parts.append("有头调试")
        if self._force_browser is True:
            mode_parts.append("强制浏览器")
        elif self._force_browser is False:
            mode_parts.append("只用 httpx")

        if mode_parts:
            self.printer.info(f"浏览器模式: {' + '.join(mode_parts)}")
        else:
            self.printer.info("浏览器模式: 自动检测")

    def _print_agent_mode(self) -> None:
        """显示当前 Agent 模式"""
        if self._use_agent_mode:
            self.printer.info("Agent 模式: 🤖 智能 Agent (LLM 决策 + Think-Act-Observe-Reflect)")
        else:
            self.printer.info("Agent 模式: 📋 旧版工作流 (关键词匹配)")

    # ---- 命令处理 ----

    def _handle_command(self, raw: str) -> None:
        parts = raw.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        handler = self._commands.get(cmd)
        if handler is None:
            self.printer.error(f"未知命令: {cmd}。输入 /help 查看可用命令。")
            return

        try:
            handled, reply = handler(arg, self)
        except CancelledError:
            print("\n  已取消，返回主菜单")
            return

        if reply:
            self.printer.reply(reply)

    # ---- 各命令 ----

    @staticmethod
    def _cmd_help(_arg: str, ui: "TerminalUI") -> tuple[bool, str | None]:
        cmds = [
            ("/help", "显示帮助", ""),
            ("/models", "列出所有模型", ""),
            ("/use_model <名称>", "切换当前模型", ""),
            ("/add_model", "交互式添加模型（openai / anthropic）", ""),
            ("/rm_model <名称>", "删除模型", ""),
            ("/skills", "查看爬虫技能矩阵", ""),
            ("/export [json|csv|md]", "导出最近一次爬取结果", ""),
            ("/debug", "切换有头调试模式（显示浏览器窗口）", ""),
            ("/browser [on|off|auto]", "强制/禁用/自动浏览器模式", ""),
            ("/agent [on|off]", "切换智能 Agent 模式（LLM 决策）", ""),
            ("/clear", "清屏", ""),
            ("/exit", "退出程序", ""),
        ]
        ui.printer.list_box("📖 命令帮助", cmds)
        return True, None

    @staticmethod
    def _cmd_models(_arg: str, ui: "TerminalUI") -> tuple[bool, str | None]:
        rows: list[tuple[str, str, str]] = []
        current = ui.llm_factory.current_name()
        for name, display, provider in ui.llm_factory.list_models():
            mark = "← 当前" if name == current else ""
            rows.append((name, display, mark))
        if not rows:
            return True, "还没有模型。先 /add_model 添加一个吧。"
        ui.printer.list_box("🤖 模型列表", rows)
        return True, None

    @staticmethod
    def _cmd_use_model(arg: str, ui: "TerminalUI") -> tuple[bool, str | None]:
        if not arg:
            return True, "用法: /use_model <模型别名>。输入 /models 看可用。"
        ok, msg = ui.llm_factory.switch_to(arg)
        return True, msg

    @staticmethod
    def _cmd_add_model(_arg: str, ui: "TerminalUI") -> tuple[bool, str | None]:
        ok, msg = _ask_new_model_interactive(ui.settings.models_file)
        if not ok:
            # 取消操作时不显示错误
            return True, None
        if msg:
            # 重新加载 settings + factory
            ui.settings = load_settings(ui.settings.models_file)
            ui.llm_factory = LLMFactory(ui.settings)
            ui.workflow = CrawWorkflow(ui.settings, ui.llm_factory)
        return True, msg

    @staticmethod
    def _cmd_rm_model(arg: str, ui: "TerminalUI") -> tuple[bool, str | None]:
        if not arg:
            return True, "用法: /rm_model <模型别名>"
        ok, msg = ui.settings.remove_model(arg)
        if ok:
            # 重新加载
            ui.settings = load_settings(ui.settings.models_file)
            ui.llm_factory = LLMFactory(ui.settings)
            ui.workflow = CrawWorkflow(ui.settings, ui.llm_factory)
        return True, msg

    @staticmethod
    def _cmd_skills(_arg: str, ui: "TerminalUI") -> tuple[bool, str | None]:
        """显示已加载的 Skill 列表"""
        skills = load_all_skills()
        if not skills:
            return True, "暂无已加载的 Skill。内置 Skill 位于 crawagent/agent/builtins/"

        rows = []
        for s in skills:
            tags = ", ".join(s.tags[:3]) if s.tags else ""
            rows.append((s.name, s.description[:40], tags))

        ui.printer.list_box(f"🎯 已加载 Skill ({len(skills)} 个)", rows)
        ui.printer.info("提示：用户 Skill 可放至 ~/.crawagent/skills/ 目录")
        return True, None

    @staticmethod
    def _cmd_skill_new(arg: str, ui: "TerminalUI") -> tuple[bool, str | None]:
        """创建新 Skill（交互式引导）"""
        if not arg:
            return True, "用法: /skill_new <skill_name>\n示例: /skill_new my_video_downloader"

        # 简化版：直接创建骨架文件
        user_dir = Path.home() / ".crawagent" / "skills"
        user_dir.mkdir(parents=True, exist_ok=True)

        skill_file = user_dir / f"{arg}.md"
        if skill_file.exists():
            return True, f"Skill 文件已存在：{skill_file}"

        # 写入骨架
        skeleton = f"""# Skill: {arg}

name: {arg}
description: 请填写 Skill 描述
trigger_keywords: [关键词1, 关键词2]
examples:
  - "示例用户输入1"
  - "示例用户输入2"

prompt: |
  请填写 Skill 的执行逻辑：
  1. 步骤1
  2. 步骤2
  3. 步骤3

tags: [标签1, 标签2]
version: 1.0
"""
        skill_file.write_text(skeleton, encoding="utf-8")
        ui.printer.info(f"已创建 Skill 骨架文件：{skill_file}")
        ui.printer.info("请编辑该文件，填写具体内容后重启 TerminalUI")
        return True, None

    @staticmethod
    def _cmd_export(arg: str, ui: "TerminalUI") -> tuple[bool, str | None]:
        state = ui._last_state
        if state is None or not state.extracted_data:
            return True, "还没有可导出的数据。先去爬点什么吧 🕷️"
        fmt = (arg or "json").lower()
        output_dir = ui.settings.output_dir
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        try:
            if fmt == "csv":
                path = export_csv(state.extracted_data, output_dir / f"crawl-{timestamp}.csv")
            elif fmt in ("md", "markdown"):
                path = export_markdown(state.extracted_data, f"CrawlAgent 导出 - {state.user_input[:30]}", output_dir / f"crawl-{timestamp}.md")
            else:
                path = export_json(state.extracted_data, output_dir / f"crawl-{timestamp}.json")
            return True, f"✅ 已导出到: {path}"
        except Exception as e:
            return True, f"❌ 导出失败: {e}"

    @staticmethod
    def _cmd_debug(_arg: str, ui: "TerminalUI") -> tuple[bool, str | None]:
        """切换有头调试模式"""
        ui._debug_mode = not ui._debug_mode
        mode = "开启" if ui._debug_mode else "关闭"
        ui.printer.info(f"有头调试模式: {mode}（将{'显示' if ui._debug_mode else '隐藏'}浏览器窗口）")
        return True, None

    @staticmethod
    def _cmd_browser(arg: str, ui: "TerminalUI") -> tuple[bool, str | None]:
        """控制浏览器模式: on/off/auto"""
        arg = arg.strip().lower()
        if not arg or arg == "auto":
            ui._force_browser = None
            return True, "浏览器模式: 自动检测（自动选择 httpx 或 Playwright）"
        elif arg == "on":
            ui._force_browser = True
            return True, "浏览器模式: 强制使用 Playwright"
        elif arg == "off":
            ui._force_browser = False
            return True, "浏览器模式: 只使用 httpx（不使用浏览器）"
        else:
            return True, "用法: /browser [on|off|auto]，当前模式: auto（自动检测）"

    @staticmethod
    def _cmd_wallpaper(arg: str, ui: "TerminalUI") -> tuple[bool, str | None]:
        """壁纸爬虫配置: /wallpaper [count=N] [dir=PATH]

        示例:
          /wallpaper              → 显示当前配置
          /wallpaper count=20     → 最多下载 20 个
          /wallpaper dir=my_imgs  → 输出到 my_imgs/
          /wallpaper count=30 dir=my_wallpapers → 同时设置
        """
        arg = arg.strip()

        if not arg:
            msg = (f"壁纸爬虫配置:\n"
                   f"  最多下载: {ui._max_wallpapers} 个\n"
                   f"  输出目录: {ui._image_output_dir}/\n"
                   f"  用法: /wallpaper [count=N] [dir=PATH]")
            return True, msg

        count_new = ui._max_wallpapers
        dir_new = ui._image_output_dir
        changed = False

        for token in arg.split():
            key, _, val = token.partition("=")
            key = key.strip().lower()
            val = val.strip()

            if key == "count" and val:
                try:
                    n = int(val)
                    if n < 1:
                        return True, "count 必须 ≥ 1"
                    if n > 100:
                        return True, "count 最多 100（避免下载过久）"
                    count_new = n
                    changed = True
                except ValueError:
                    return True, f"count 值无效: {val}"
            elif key == "dir" and val:
                dir_new = val.strip().rstrip("/").rstrip("\\")
                changed = True
            else:
                return True, f"未知参数: {token}。用法: /wallpaper [count=N] [dir=PATH]"

        if changed:
            ui._max_wallpapers = count_new
            ui._image_output_dir = dir_new
            return True, f"壁纸爬虫配置已更新: 最多 {count_new} 个 → {dir_new}/"

        return True, "用法: /wallpaper [count=N] [dir=PATH]"

    @staticmethod
    def _cmd_video(arg: str, ui: "TerminalUI") -> tuple[bool, str | None]:
        """视频爬虫配置: /video [dir=PATH]

        说明: 主流视频网站(优酷/爱奇艺/B站/腾讯视频)的视频流多为加密协议,
        CrawAgent 当前专注于提取可访问的元数据(标题/简介/演员/剧集/标签等),
        并尽可能检测 m3u8/mp4 等可下载的流地址。

        示例:
          /video                    → 显示当前配置
          /video dir=my_videos      → 元数据保存到 my_videos/
        """
        arg = arg.strip()

        if not arg:
            msg = (f"🎬 视频爬虫配置:\n"
                   f"   元数据输出目录: {ui._video_output_dir}/\n"
                   f"   适用站点: 优酷/爱奇艺/B站/腾讯视频/YouTube/抖音/芒果TV 等\n"
                   f"   功能: 自动检测视频页面 → 提取标题/简介/演员/剧集/标签/流地址\n"
                   f"   注意: 加密的 VIP/会员视频仅能提取元数据, 无法获取可下载的直链\n"
                   f"   用法: /video [dir=PATH]")
            return True, msg

        dir_new = ui._video_output_dir
        changed = False

        for token in arg.split():
            key, _, val = token.partition("=")
            key = key.strip().lower()
            val = val.strip()

            if key == "dir" and val:
                dir_new = val.strip().rstrip("/").rstrip("\\")
                changed = True
            else:
                return True, f"未知参数: {token}。用法: /video [dir=PATH]"

        if changed:
            ui._video_output_dir = dir_new
            return True, f"🎬 视频爬虫配置已更新: 元数据输出 → {dir_new}/"

        return True, "用法: /video [dir=PATH]"

    @staticmethod
    def _cmd_agent(arg: str, ui: "TerminalUI") -> tuple[bool, str | None]:
        """切换 Agent 模式: /agent [on|off]

        新版 Agent 工作流特点:
        - LLM 参意图解析和工具选择决策
        - Think → Act → Observe → Reflect 循环
        - 失败可自动重试或换用其他工具
        - LLM 反思执行结果，决定是否需要调整策略

        旧版工作流特点:
        - 关键词匹配意图
        - 固定顺序执行
        - 无重试机制

        示例:
          /agent          → 显示当前模式
          /agent on       → 启用智能 Agent 模式
          /agent off      → 使用旧版工作流
        """
        arg = arg.strip().lower()

        if not arg:
            mode = "智能 Agent (LLM 决策)" if ui._use_agent_mode else "旧版工作流 (关键词匹配)"
            return True, (
                f"🤖 Agent 模式: {mode}\n"
                f"   新版特点: LLM 决策 + Think-Act-Observe-Reflect 循环 + 自动重试\n"
                f"   旧版特点: 关键词匹配 + 固定顺序执行\n"
                f"   用法: /agent [on|off]"
            )

        if arg == "on":
            ui._use_agent_mode = True
            ui._print_agent_mode()
            return True, "✅ 已启用智能 Agent 模式（LLM 决策 + Think-Act-Observe-Reflect）"
        elif arg == "off":
            ui._use_agent_mode = False
            ui._print_agent_mode()
            return True, "✅ 已切换到旧版工作流（关键词匹配）"
        else:
            return True, "用法: /agent [on|off]"

    @staticmethod
    def _cmd_clear(_arg: str, ui: "TerminalUI") -> tuple[bool, str | None]:
        print("\033c", end="")
        ui.printer.banner()
        return True, None

    @staticmethod
    def _cmd_exit(_arg: str, ui: "TerminalUI") -> tuple[bool, str | None]:
        ui.printer.info("再见！👋")
        sys.exit(0)
