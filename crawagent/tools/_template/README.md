# <插件名>（CrawAgent 插件）

CrawAgent 第三方插件。目录约定：

```
<plugin-name>/
  plugin.json     ← manifest（必填 name；可选 version/description/author/dependencies）
  tools/*.py      ← 工具模块（可选）。模块级 @tool / @register_tool 函数自动进 Agent 工具表
  skills/*/SKILL.md ← 技能包（可选）。自动进 Agent 技能索引（read_skill 可读）
  requirements.txt  ← pip 依赖清单（可选，仅提示用，需手动安装）
  README.md       ← 本文件
```

## 安装

把本目录放进 CrawAgent 项目根的 `plugins/` 下（或用 `crawagent add-plugin <路径|git URL>` 一键接入），
重启后端即生效；`crawagent plugins` 可查看已装插件。

## 开发

从零创建同款结构：`crawagent add-tool <your_plugin_name>`。
