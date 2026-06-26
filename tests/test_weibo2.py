"""测试优化后的提取效果"""
import sys
sys.path.insert(0, '.')

from crawagent.config.settings import load_settings
from crawagent.llm.factory import LLMFactory
from crawagent.graph.agent_workflow import AgentWorkflow
from crawagent.tools.database import get_default_db

print('=' * 60)
print('测试：优化后的微博热搜提取')
print('=' * 60)

# 先清掉之前的测试数据
db = get_default_db()
deleted = db.delete(source='weibo')
print(f'\n清理旧数据: 删除了 {deleted} 条\n')

settings = load_settings()
llm_factory = LLMFactory(settings)
agent = AgentWorkflow(settings, llm_factory)

user_input = '帮我抓取"https://weibo.com/newlogin?tabtype=weibo&gid=102803&openLoginLayer=0&url="网页综合页的所有信息保存到数据库'

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
    print(result.get('tool_result', '')[:3000])
    
    # 检查数据库
    print('\n' + '=' * 60)
    print('数据库检查:')
    print('=' * 60)
    records = db.query(source='weibo', limit=10)
    print(f'微博来源共 {db.count(source="weibo")} 条记录\n')
    for i, r in enumerate(records[:10], 1):
        title = r.title[:60] if r.title else '(no title)'
        print(f'  {i}. {title}')
        if r.hot:
            print(f'     热度: {r.hot}')
        if r.rank:
            print(f'     排名: {r.rank}')
    
except Exception as e:
    print(f'\n执行出错: {e}')
    import traceback
    traceback.print_exc()
