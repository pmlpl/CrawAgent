# 变更 018：`_social_utils.py` 去下划线前缀重命名为 `social_utils.py`

| 项目 | 内容 |
|------|------|
| 变更编号 | 018 |
| 提出日期 | 2026-09-20 |
| 状态 | 待批准 |
| 类型 | 命名规范 |
| 关联模块 | `crawagent/tools/_social_utils.py`（重命名为 `social_utils.py`） |
| 来源 | `docs/tech-debt/2026-09-20.md` P3 #12 |

---

## 〇、价值

- **改动前**：`crawagent/tools/_social_utils.py` 用下划线前缀（按 Python 约定表示"模块私有"），但被 `bilibili_tool.py:16`、`douyin_tool.py:9`、`social_tool.py:19` 直接 import 当公共 helper 用。`social_tool.py:19` 还专门 `# noqa: F401  (re-export 供既有调用方使用)` 抑制 lint 警告。下划线前缀与"实际公开"的语义矛盾，新作者会以为是内部 helper 而避开，造成命名混乱。
- **改动后**：模块重命名为 `social_utils.py`（无前缀），文件级 public helper 名实相符。`noqa: F401` 注释保留（仍是有意的 re-export）。

---

## 一、背景与问题

1. Python 下划线前缀 `_module` 约定：模块不对外，仅作内部 helper。
2. 现状：`_social_utils.py` 含 3 个函数（`_get` / `_resolve` / `_merge_media`）+ 1 个 `from crawagent.config.settings import get_settings` 的导入。但实际被 3 个工具文件直接 import 公共使用。
3. 命名债务：从字面看是私有，从行为看是公开 → 误导。

---

## 二、目标

1. **重命名文件**：`crawagent/tools/_social_utils.py` → `crawagent/tools/social_utils.py`
2. **更新 import 引用**：`bilibili_tool.py`、`douyin_tool.py`、`social_tool.py` 三处 `from crawagent.tools._social_utils import` → `from crawagent.tools.social_utils import`
3. **保留** noqa 注释（仍然是有意的 re-export，不应触发 F401）
4. **不动** 函数实现本身

---

## 三、方案设计

```
git mv crawagent/tools/_social_utils.py crawagent/tools/social_utils.py

# 三处 import 替换：
sed -i 's/crawagent\.tools\._social_utils/crawagent.tools.social_utils/g' \
  crawagent/tools/bilibili_tool.py \
  crawagent/tools/douyin_tool.py \
  crawagent/tools/social_tool.py
```

`registry.py` 的 `discover_tools()` 会扫 `crawagent/tools/*.py` 但**只看 BaseTool 实例**，不影响模块名。

---

## 四、涉及文件清单

| 操作 | 文件 |
|------|------|
| 重命名 | `crawagent/tools/_social_utils.py` → `social_utils.py` |
| 修改 | `crawagent/tools/bilibili_tool.py`（1 行 import） |
| 修改 | `crawagent/tools/douyin_tool.py`（1 行 import） |
| 修改 | `crawagent/tools/social_tool.py`（1 行 import） |

---

## 五、验证方式

1. **全量 pytest 全过**（基线 399，应不变）：`uv run pytest tests/ -q` → ≥399 passed
2. **静态检查**：`grep -rn "_social_utils" crawagent/ tests/` 应无命中
3. **三个工具仍可调用**：`bilibili_extract`、`douyin_extract`、`extract_social_media` 三个 `@tool` 函数的 `func` 不抛 ImportError

---

## 六、后续可扩展（不在本次范围）

- 把 `_social_utils.py` 里的 `_get` / `_resolve` 函数前缀也去掉（已是 public 名实相符）
- 如果将来需要把 B 站 / 抖音 / 微博合到一个 `social/` 子目录里（按平台分文件），可下个规格

---

## 七、实施顺序建议

1. `git mv` 重命名文件
2. 三个 import 行 sed 替换
3. 跑全量 pytest 确认
4. 写 §八 实施记录

---

## 八、实施记录

**实施日期**：2026-09-20
**实施人**：Agent（指挥官 Joker 批准）
**关联提交**：本批次（018-022 + 批次 2 共 14 项）一次 commit

### 8.1 实际改动

| 序号 | 操作 | 文件 | 说明 |
|------|------|------|------|
| 1 | 重命名 | `crawagent/tools/_social_utils.py` → `social_utils.py` | `git mv` 保留 history |
| 2 | 修改 | `crawagent/tools/bilibili_tool.py:16` | `from crawagent.tools._social_utils import (` → `from crawagent.tools.social_utils import (` |
| 3 | 修改 | `crawagent/tools/douyin_tool.py:9` | 同上 |
| 4 | 修改 | `crawagent/tools/social_tool.py:9` | 注释 "通用 HTTP/下载工具搬至 _social_utils.py" → "social_utils.py" |
| 5 | 修改 | `crawagent/tools/social_tool.py:19` | import 路径 + noqa 注释保留 |
| 6 | 修改 | `crawagent/tools/registry.py:182` | `_SKIP_TOOL_MODULES` 集合 `"_social_utils"` → `"social_utils"`，注释从"内部工具"改为"公共 helper 模块（无 BaseTool，避免被自动收集）" |

> **注**：规格 §四 原列 3 处 import，实际实施时发现 `_SKIP_TOOL_MODULES` 和 `social_tool.py:9` 注释也含 `_social_utils` 引用，必须同步更新，否则产生未来序列化路径/扫描器 bug。共 5 处改动（+ 文件重命名）。

### 8.2 验证结果

- **`grep -rn "_social_utils" crawagent/ tests/`**：无命中 ✓
- **`uv run pytest tests/ -q`**：399 passed, 6 warnings in 72.12s ✓（基线 399 不变）
- **`git status`**：5 处 M + 1 处 R（重命名）状态正确

### 8.3 实施经验

1. **`_SKIP_TOOL_MODULES` 是规格盲区**——grep 引用要扫全仓（包括集合/注释），不能只看 import 行
2. **git mv 跨平台兼容**——PowerShell 默认不识别 `&&` 分隔符（需用 `;` 或单条），但 git 子命令本身正常
3. **`social_tool.py:9` 注释"通用 HTTP/下载工具搬至 _social_utils.py"**——这种行内文档也会被 grep 命中，重命名时一并改

### 8.4 后续

按计划推进 019（file_tool symlink 安全）。本批 14 项整体 commit（per 指挥官确认）。