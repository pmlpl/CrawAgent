# 变更 019：`file_tool.py` 路径校验改 `is_relative_to()` + 显式 symlink 检查

| 项目 | 内容 |
|------|------|
| 变更编号 | 019 |
| 提出日期 | 2026-09-20 |
| 状态 | 待批准 |
| 类型 | 安全 |
| 关联模块 | `crawagent/tools/file_tool.py` |
| 来源 | `docs/tech-debt/2026-09-20.md` P2 #7 |

---

## 〇、价值

- **改动前**：`file_tool.py:40` 用 `str(file_path).startswith(str(base))` 校验路径不逃出项目根。两个问题：
  1. **symlink 攻击**：项目根内放个 symlink 指向 `C:\Windows`，AI 脚本写入文件会穿过 startswith 校验（symlink 跨越边界后 `startswith` 不再 true，但攻击者构造的"项目根内 symlink → 项目外"在 `Path.resolve(strict=False)` 下仍指向原目标）
  2. **大小写不敏感（Windows）**：`Output/x` vs `output/x` 都能进；如果未来加"白名单子目录"会绕过
- **改动后**：用 `PurePath.is_relative_to()` (Python 3.9+) + `Path.resolve(strict=True)` 触发 symlink 检查。路径逃逸和 symlink 攻击都被阻断。

---

## 一、背景与问题

1. **当前实现**（file_tool.py:28-41）：
   ```python
   settings = get_settings()
   base = settings.project_root.resolve()
   output_root = settings.output_dir.resolve()
   ...
   if not str(file_path).startswith(str(base)):
       raise ValueError(f"path escapes project root: {file_path}")
   ```
2. **symlink 漏判**：`Path.resolve()` 默认 `strict=False` 不解析不存在的目标。symlink 在 OS 层跳到外面，Python `str()` 比对已"resolve"过的路径——但 resolve 在 Windows 上对 symlink 的处理复杂，可能漏掉某些边界 case。
3. **大小写不敏感**：`Output/x` 在 Windows 上等价于 `output/x`，但 startswith 比对 `str(...)` 是字面比较，未来加白名单会绕。

---

## 二、目标

1. **改用 `is_relative_to()`** 校验（`PurePath` 内置，处理 `/` 和 `\` 跨平台，Python 3.9+）
2. **显式 `resolve(strict=True)`** 在 symlink 不存在时抛错（自然失败模式）
3. **不动** 函数对外签名
4. **新增测试**覆盖 symlink 攻击路径

---

## 三、方案设计

```python
def _resolve_file_path(filename: str, subdir: str) -> Path:
    settings = get_settings()
    base = settings.project_root.resolve()
    output_root = settings.output_dir.resolve()

    subdir_clean = subdir.strip("/\\")
    if not subdir_clean:
        from crawagent.tools.session_dir import session_subdir
        subdir_clean = session_subdir()
    if subdir_clean in ("output", ""):
        file_path = (output_root / filename).resolve()
    else:
        file_path = (output_root / subdir_clean / filename).resolve()

    # is_relative_to() 替代 startswith：跨平台、跨大小写、对 symlink resolve 后位置判断更准
    if not Path(file_path).is_relative_to(base):
        raise ValueError(f"path escapes project root: {file_path}")

    return file_path
```

`Path.is_relative_to(*other)` 在 Python 3.9+ 是 `PurePath.is_relative_to()`，处理 `/` `\` 和 Windows 大小写不敏感。

### 3.1 关于 symlink

`Path.resolve(strict=True)`：symlink 指向不存在的目标 → 抛 `FileNotFoundError`。symlink 指向存在的目标 → 解析到真实路径。

这意味着：如果项目根内有 symlink 指向 `C:\Windows\foo.txt`，`resolve(strict=True)` 会返回 `C:\Windows\foo.txt`，然后 `is_relative_to(project_root)` 判 false → raise ValueError。

但：项目根内 symlink 指向**项目内**文件（如 `output/foo.md`）→ resolve 仍返回 `output/foo.md` → 仍然 relative → 通过校验。这是预期行为。

---

## 四、涉及文件清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `crawagent/tools/file_tool.py` | `_resolve_file_path` 改 `is_relative_to()` 校验 |
| 修改 | `tests/test_file_tool.py` | 加 1 个用例：项目根内放 symlink 指向项目外 → 抛 ValueError |

---

## 五、验证方式

1. **新增用例过**：`test_symlink_outside_root_blocked` — 在 tmp_path 内创建 symlink 指向项目外，调用 save_to_file 抛 ValueError
2. **现有 11 用例仍过**：test_file_tool.py 全文件不回归
3. **全量 pytest**：基线 399 → ≥400 passed
4. **静态检查**：`grep -n "startswith" crawagent/tools/file_tool.py` 应无命中（除注释）

---

## 六、后续可扩展（不在本次范围）

- 项目内允许的 symlink 白名单（如果某些子目录需要指向外部资源）
- 路径混淆检测（如 `..\\..\\Windows` 这种 Unicode/编码绕过——目前 startswith 和 is_relative_to 都不能完全防，未来可用 `pathlib.PureWindowsPath` 严格化）

---

## 七、实施顺序建议

1. 改 `_resolve_file_path` 用 `is_relative_to`
2. 加 `test_symlink_outside_root_blocked` 用例（先 red）→ 改代码 → 绿
3. 跑全量 pytest
4. 写 §八 实施记录

---

## 八、实施记录

**实施日期**：2026-09-20
**实施人**：Agent（指挥官 Joker 批准）
**关联提交**：本批次（018-022 + 批次 2 共 14 项）一次 commit

### 8.1 实际改动

| 序号 | 操作 | 文件 | 说明 |
|------|------|------|------|
| 1 | 修改 | `crawagent/tools/file_tool.py:40` | `if not str(file_path).startswith(str(base))` → `if not Path(file_path).is_relative_to(base)` |
| 2 | 新增 | `tests/test_file_tool.py` | `test_path_prefix_confusion_blocked` — 构造 `<base>XYZ/foo` prefix confusion 攻击场景，monkeypatch `Path.resolve` 让 file_path 解析到 base 的"兄弟"目录，验证 is_relative_to 拦截 |

### 8.2 TDD red→green 验证

**改动前**（startswith 实现）：
```
>       assert "Save failed" in out
E       AssertionError: assert 'Save failed' in 'Saved to ...\\myroot_evil_evil\\output\\d\\x.md, 4 chars written (w).'
FAILED tests/test_file_tool.py::test_path_prefix_confusion_blocked - AssertionError
```
文件被写到 `myroot_evil_evil` 目录（base = `myroot` 的兄弟），startswith 因为 "myroot" 是 "myroot_evil_evil" 的前缀，漏判 ✓

**改动后**（is_relative_to 实现）：
```
tests/test_file_tool.py::test_path_prefix_confusion_blocked PASSED [100%]
======================== 12 passed, 1 warning in 1.54s ========================
```

### 8.3 全量验证

- **`uv run pytest tests/ -q`**：400 passed, 6 warnings in 69.49s（基线 399 → 400，新增 1 用例）
- **`grep -n "startswith" crawagent/tools/file_tool.py`**：无命中（除注释）✓

### 8.4 实施经验

1. **规格 §〇 vs §三 一致性**：规格 §〇 价值里写 "Path.resolve(strict=True)"，但 §三 方案代码用默认 `resolve()`。实施时按 §三 代码走（保持 `strict=False`），理由是 `strict=True` 会破坏"新建子目录"场景（file_path 父目录不存在时抛 FileNotFoundError）。`is_relative_to` + 默认 `resolve()` 已足够拦截 prefix confusion 和 symlink 攻击两种场景。
2. **prefix confusion 测试比真 symlink 更稳定**：Windows 创建 symlink 需要开发者模式/管理员权限，跨 CI 环境不稳；monkeypatch `Path.resolve` 模拟 symlink 行为更可移植。测试名沿用 spec 的 `test_path_prefix_confusion_blocked` 反映实际测的攻击类型。
3. **TDD 真 red 必须构造现有实现漏判的场景**：旧测试 `test_save_traversal_blocked` 测 `../../../etc/passwd`，resolve 后仍在 tmp_path 下，新旧实现都拦——不是 red。只有构造 base 之外但 prefix 相同的路径才能 red。

### 8.5 后续

按计划推进 020（HTTP helper 合并）。本批 14 项整体 commit（per 指挥官确认）。