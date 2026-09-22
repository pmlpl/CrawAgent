# 变更 023：续补 18 工具测试覆盖（016 P0 #2 第二批）

| 项目 | 内容 |
|------|------|
| 变更编号 | 023 |
| 提出日期 | 2026-09-20 |
| 状态 | 待批准 |
| 类型 | 测试 |
| 关联模块 | 18 个工具测试（详见 §三） |
| 来源 | `docs/tech-debt/2026-09-20.md` P0 #2 第二批 |

---

## 〇、价值

- **改动前**：批次 1 (016) 补了 5 个核心工具测试（file_tool / registry / script_tool / mcp_capture / crawl_tool）。仍缺 ~18 个工具单测：browse / wallpaper / ask_user / search / weread / download_images / login / proxy / video_probe / save / extract / list_extract / site_profile / site_analyze / bilibili / douyin / social / font_decrypt
- **改动后**：覆盖率从 5/33 → 23/33（约 70%）；剩余 10 个为 Phase 3 子 Agent / 模板类，可放后续

---

## 一、背景与问题

1. P0 #2 原列表 23 个无测工具，016 补 5 个，余 18 个
2. 每工具平均 8-10 个用例（核心 + 边界 + 错误路径）
3. 优先级矩阵：**路径安全 > 注册中心 > 沙箱 > 主流入口 > 工具细节**

---

## 二、目标

| 优先级 | 工具 | 用例估算 | 备注 |
|--------|------|----------|------|
| **P0** | `ask_user_tool.py` | 10 | 关键交互（agent 用户决策回路） |
| **P0** | `browse_tool.py` | 10 | Playwright 主流入口 |
| **P0** | `wallpaper_tool.py` | 8 | ADR-0001 落点，写背景图 |
| **P1** | `weread_tool.py` | 12 | 多端点（list + get_chapter）+ 错误 |
| **P1** | `login_tool.py` | 10 | Cookie 探活 + 错误 |
| **P1** | `proxy_tool.py` | 8 | 池 + 健康检查 |
| **P1** | `search_tool.py` | 8 | DDG + Baidu fallback |
| **P1** | `video_probe_tool.py` | 12 | m3u8 解析 + 媒体检测 |
| **P1** | `download_images.py` | 8 | stream 多图下载 |
| **P2** | `bilibili_tool.py` | 10 | WBI 签名 + 端点（与 029 协调） |
| **P2** | `douyin_tool.py` | 8 | 链接解析 + 抽取 |
| **P2** | `social_tool.py` | 6 | 转发层 |
| **P2** | `social_utils.py` | 6 | HTTP / 下载 helper（020 已部分覆盖） |
| **P2** | `extract_tool.py` | 6 | 主流入口 |
| **P2** | `save_tool.py` | 6 | 写记录 |
| **P2** | `list_extract_tool.py` | 8 | 列表解析（+ signature/container_depth） |
| **P3** | `site_profile_tool.py` | 8 | Phase 3 |
| **P3** | `site_analyze_tool.py` | 6 | Phase 3 子 Agent 入口 |

总用例估算：~150（spec 原估 180-270 偏宽，实际 ~150 即可达 70% 覆盖）

---

## 三、方案设计

每工具测试统一：
- 模块级 fixture（参考 test_file_tool.py fake_settings 模式）
- 核心成功路径 3-5 用例
- 边界 / 错误路径 2-3 用例
- mock 外部依赖（HTTP / 文件 / Playwright）

### 3.1 通用 fixture 模式

```python
@pytest.fixture
def fake_settings(tmp_path, monkeypatch):
    class _S:
        project_root = tmp_path
        output_dir = tmp_path / "output"
    (tmp_path / "output").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(tool_module, "get_settings", lambda: _S())
    return _S
```

### 3.2 HTTP mock 模式（统一用 `_http.http_get`）

```python
with patch("crawagent.tools._http.requests.get") as mock_get:
    mock_get.return_value = _mock_response("<html>...</html>")
    out = tool.func(...)
```

---

## 四、涉及文件清单

| 操作 | 文件 |
|------|------|
| 新增 | `tests/test_ask_user_tool.py` |
| 新增 | `tests/test_browse_tool.py` |
| 新增 | `tests/test_wallpaper_tool.py` |
| 新增 | `tests/test_weread_tool.py` |
| 新增 | `tests/test_login_tool.py` |
| 新增 | `tests/test_proxy_tool.py` |
| 新增 | `tests/test_search_tool.py` |
| 新增 | `tests/test_video_probe_tool.py` |
| 新增 | `tests/test_download_images.py` |
| 新增 | `tests/test_bilibili_tool.py`（与 029 协调） |
| 新增 | `tests/test_douyin_tool.py` |
| 新增 | `tests/test_social_tool.py` |
| 新增 | `tests/test_social_utils.py` |
| 新增 | `tests/test_extract_tool.py` |
| 新增 | `tests/test_save_tool.py` |
| 新增 | `tests/test_list_extract_tool.py` |
| 新增 | `tests/test_site_profile_tool.py` |
| 新增 | `tests/test_site_analyze_tool.py` |

---

## 五、验证方式

- **全量 pytest**：410 → 560 passed（+150 用例）
- **覆盖率脚本**：`scripts/_audit_docstrings.py` 类新增 `scripts/_audit_tool_tests.py` 验证 23/33 工具被覆盖

---

## 六、后续可扩展（不在本次范围）

- Phase 3 子 Agent（advanced_tools / android_reverse / quality_filter）的子测试
- 跨工具集成测试（如 bilibili_extract → download 端到端）

---

## 七、实施顺序建议

1. 按 P0 → P1 → P2 → P3 顺序
2. 每工具一组测试文件，独立 commit 不混
3. 跑全量 pytest 确认无回归

---

## 八、实施记录

> 待批准后由 Agent 实施时填写。