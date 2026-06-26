"""测试：新浪军事内容抓取"""
import sys
sys.path.insert(0, '.')

from crawagent.config.settings import load_settings
from crawagent.llm.factory import LLMFactory
from crawagent.graph.agent_workflow import AgentWorkflow
from crawagent.tools.database import get_default_db

print('=' * 60)
print('测试：新浪军事内容抓取')
print('=' * 60)

db = get_default_db()
deleted = db.delete(source='sina')
print(f'\n清理旧数据: 删除了 {deleted} 条\n')

settings = load_settings()
llm_factory = LLMFactory(settings)
agent = AgentWorkflow(settings, llm_factory)

user_input = '帮我抓取"https://www.sina.com.cn/"网页中的军事内容，保存到数据库'

print(f'用户输入: {user_input}\n')

try:
    result = agent.run(
        user_input,
        max_iterations=3,
    )
    
    print('\n' + '=' * 60)
    print('执行结果:')
    print('=' * 60)
    print(f'选择的工具: {result.get("selected_tool", "未知")}')
    print(f'工具参数: {result.get("tool_args", {})}')
    print(f'是否成功: {result.get("tool_success", False)}')
    print(f'\n工具结果:')
    tool_result = result.get('tool_result', '')
    print(tool_result[:2500])
    
    # 检查数据库
    print('\n' + '=' * 60)
    print('数据库检查:')
    print('=' * 60)
    records = db.query(source='sina', limit=15)
    total = db.count(source='sina')
    print(f'新浪来源共 {total} 条记录\n')
    for i, r in enumerate(records[:15], 1):
        title = r.title[:70] if r.title else '(no title)'
        cat = f' [{r.category}]' if r.category else ''
        print(f'  {i}. {title}{cat}')
    
except Exception as e:
    print(f'\n执行出错: {e}')
    import traceback
    traceback.print_exc()
