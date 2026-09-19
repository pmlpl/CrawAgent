# 冒烟测试：script_tool.py 能正确导入且不在 raw string 模板边界处炸掉。
# 这个文件有 1107 行，其中 600+ 行是 _ALLOWED_HEADER = r'''...''' raw string 模板，
# 之前曾因代码插入到 raw string 内部导致 SyntaxError。
# 核心目的：确保 import 不报错 + run_custom_script 工具存在 + DATA_TMP_DIR 正确。
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_script_tool_imports_cleanly():
    """import script_tool 不抛 SyntaxError / ImportError。"""
    import crawagent.tools.script_tool as st
    assert st is not None
    # 模块级常量应该存在
    assert hasattr(st, "_ALLOWED_HEADER")
    assert isinstance(st._ALLOWED_HEADER, str)
    assert len(st._ALLOWED_HEADER) > 100, "_ALLOWED_HEADER raw string 模板内容应该很长"


def test_run_custom_script_is_tool():
    """run_custom_script 是一个 langchain @tool。"""
    from crawagent.tools.script_tool import run_custom_script
    assert run_custom_script is not None
    assert hasattr(run_custom_script, "name")
    assert run_custom_script.name == "run_custom_script"
    assert hasattr(run_custom_script, "description")
    assert "脚本" in run_custom_script.description or "script" in run_custom_script.description.lower()


def test_run_custom_script_not_too_broken():
    """run_custom_script 的签名看起来合理（不真执行脚本）。"""
    from crawagent.tools.script_tool import run_custom_script
    # 检查 args_schema 有 code 参数
    schema = run_custom_script.args_schema
    assert schema is not None
    fields = getattr(schema, "model_fields", None) or {}
    assert "code" in fields, f"run_custom_script 应该有 code 参数，实际字段: {list(fields.keys())}"


def test_site_profile_tools_importable():
    """list_site_profiles / save_site_profile 在 site_profile_tool.py（canonical source）。"""
    from crawagent.tools.site_profile_tool import list_site_profiles, save_site_profile
    assert list_site_profiles.name == "list_site_profiles"
    assert save_site_profile.name == "save_site_profile"


def test_data_tmp_dir_exists_and_callable():
    """_ensure_tmp 函数存在且 DATA_TMP_DIR 已定义（临时文件集中化）。"""
    from crawagent.tools.script_tool import _ensure_tmp, DATA_TMP_DIR
    assert DATA_TMP_DIR is not None
    assert callable(_ensure_tmp)
    # _ensure_tmp 应该返回一个 Path
    p = _ensure_tmp()
    assert hasattr(p, "exists")


def test_ensure_tmp_removes_subdirectories():
    """_ensure_tmp 递归清空：顶层文件和子目录一起清（修前只清顶层文件，子目录残留）。

    在真实 DATA_TMP_DIR 造垃圾再验证被清——无持久副作用（_ensure_tmp 本就是清空用的）。
    """
    from crawagent.tools.script_tool import _ensure_tmp, DATA_TMP_DIR
    junk_file = DATA_TMP_DIR / "_test_junk_file.txt"
    junk_sub = DATA_TMP_DIR / "_test_junk_sub"
    junk_sub.mkdir(parents=True, exist_ok=True)
    (junk_sub / "_nested.txt").write_text("junk", encoding="utf-8")
    junk_file.write_text("junk", encoding="utf-8")

    _ensure_tmp()

    assert not junk_file.exists(), "顶层遗留文件应被清掉"
    assert not junk_sub.exists(), "子目录应被递归清掉（修前只清文件、子目录残留膨胀）"


def test_script_tool_path_is_inside_project():
    """script_tool.py 的 DATA_TMP_DIR 指向 data/_tmp（不是系统 %TEMP%）。"""
    from crawagent.tools.script_tool import DATA_TMP_DIR
    s = str(DATA_TMP_DIR).lower().replace("\\", "/")
    assert "data/_tmp" in s or "data\\_tmp" in s or ("/data/" in s and "_tmp" in s), \
        f"DATA_TMP_DIR 应该在项目 data/_tmp 下，实际: {DATA_TMP_DIR}"
