"""存储层抽象包：checkpointer 工厂 + 会话元数据 + 会话列表只读视图。

分布式爬虫功能的存储底座，sqlite/redis 后端可切换：
    - checkpointer：按 settings.checkpoint_backend 切换 sqlite/redis 后端
    - meta_store：会话标题/错误，恒走 SQLite，与 checkpointer 解耦
    - checkpoint_view：会话列表只读视图，按后端返回对应实现
"""
