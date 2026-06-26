"""数据库工具 - 支持 SQLite 和 MySQL

用法:
    from crawagent.tools.database import DatabaseManager, CrawlRecord
    
    # SQLite（默认）
    db = DatabaseManager()  # 使用 output/crawls.db
    
    # MySQL
    db = DatabaseManager(
        db_type="mysql",
        host="localhost",
        port=3306,
        user="root",
        password="xxx",
        database="crawls"
    )
    
    # 保存爬取记录
    db.save_crawl(
        url="https://example.com",
        title="示例标题",
        content="网页内容...",
        source="example",
        extra_data={"author": "作者", "date": "2024-01-01"}
    )
    
    # 查询记录
    records = db.query(url="https://example.com")
    records = db.query(source="zhihu", limit=10)
    
    # 导出到文件
    db.export_to_json("output/crawls.json")
    db.export_to_csv("output/crawls.csv")
"""
from __future__ import annotations

import json
import csv
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from dataclasses import dataclass, field, asdict
from contextlib import contextmanager

try:
    from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, JSON
    from sqlalchemy.orm import sessionmaker, declarative_base
    from sqlalchemy.pool import StaticPool
    _SQLALCHEMY_AVAILABLE = True
except ImportError:
    _SQLALCHEMY_AVAILABLE = False


@dataclass
class CrawlRecord:
    """爬取记录"""
    id: int | None = None
    url: str = ""
    title: str = ""
    content: str = ""
    source: str = ""  # 来源网站标识
    category: str = ""  # 分类
    author: str = ""  # 作者
    published_date: str = ""  # 发布时间
    created_at: str = ""
    extra_data: dict = field(default_factory=dict)  # 其他字段

    def to_dict(self) -> dict:
        return asdict(self)


class DatabaseManager:
    """数据库管理器"""
    
    def __init__(
        self,
        db_type: str = "sqlite",
        db_path: str | Path = "output/crawls.db",
        **kwargs
    ):
        """
        初始化数据库
        
        Args:
            db_type: "sqlite" 或 "mysql"
            db_path: SQLite 数据库路径
            **kwargs: MySQL 连接参数 (host, port, user, password, database)
        """
        self.db_type = db_type
        self._engine = None
        self._session_factory = None
        self._Base = None
        
        if db_type == "sqlite":
            self._init_sqlite(db_path)
        elif db_type == "mysql":
            if not _SQLALCHEMY_AVAILABLE:
                raise ImportError("需要安装 sqlalchemy: pip install sqlalchemy")
            self._init_mysql(**kwargs)
        else:
            raise ValueError(f"不支持的数据库类型: {db_type}")
        
        self._create_tables()
    
    def _init_sqlite(self, db_path: str | Path):
        """初始化 SQLite"""
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        
        if _SQLALCHEMY_AVAILABLE:
            # 使用 SQLAlchemy
            connection_string = f"sqlite:///{db_path}"
            self._engine = create_engine(
                connection_string,
                connect_args={"check_same_thread": False},
                poolclass=StaticPool
            )
            self._session_factory = sessionmaker(bind=self._engine)
            self._Base = declarative_base()
            
            # 定义表
            self._CrawlTable = self._create_sqlalchemy_table()
        else:
            # 纯 SQLite（降级方案）
            self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
    
    def _init_mysql(self, **kwargs):
        """初始化 MySQL"""
        host = kwargs.get("host", "localhost")
        port = kwargs.get("port", 3306)
        user = kwargs.get("user", "root")
        password = kwargs.get("password", "")
        database = kwargs.get("database", "crawls")
        
        connection_string = f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}"
        self._engine = create_engine(connection_string)
        self._session_factory = sessionmaker(bind=self._engine)
        self._Base = declarative_base()
        
        self._CrawlTable = self._create_sqlalchemy_table()
    
    def _create_sqlalchemy_table(self):
        """使用 SQLAlchemy 创建表"""
        from sqlalchemy import Index
        
        class CrawlTable(self._Base):
            __tablename__ = "crawl_records"
            
            id = Column(Integer, primary_key=True, autoincrement=True)
            url = Column(String(2048), nullable=False)
            title = Column(String(1024), default="")
            content = Column(Text, default="")
            source = Column(String(256), index=True)
            category = Column(String(256), default="")
            author = Column(String(256), default="")
            published_date = Column(String(128), default="")
            created_at = Column(DateTime, default=datetime.now)
            extra_data = Column(JSON, default=dict)
            
            # MySQL 需要前缀索引（utf8mb4 下 768 字符 = 3072 字节）
            __table_args__ = ()
        
        # 为 MySQL 添加前缀索引
        if self.db_type == "mysql":
            from sqlalchemy import DDL, event
            
            @event.listens_for(CrawlTable.__table__, "after_create")
            def add_mysql_indexes(target, connection, **kw):
                connection.execute(
                    DDL("CREATE INDEX ix_crawl_records_url ON crawl_records (url(768))")
                )
                connection.execute(
                    DDL("CREATE INDEX ix_crawl_records_title ON crawl_records (title(255))")
                )
        
        return CrawlTable
    
    def _create_tables(self):
        """创建表"""
        if self._engine and self._Base:
            self._Base.metadata.create_all(self._engine)
        elif hasattr(self, "_conn"):
            # 纯 SQLite
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS crawl_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL,
                    title TEXT DEFAULT '',
                    content TEXT DEFAULT '',
                    source TEXT DEFAULT '',
                    category TEXT DEFAULT '',
                    author TEXT DEFAULT '',
                    published_date TEXT DEFAULT '',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    extra_data TEXT DEFAULT '{}'
                )
            """)
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_url ON crawl_records(url)")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_source ON crawl_records(source)")
            self._conn.commit()
    
    @contextmanager
    def _get_session(self):
        """获取数据库会话"""
        if self._session_factory:
            session = self._session_factory()
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()
        else:
            yield self._conn
    
    def save_crawl(
        self,
        url: str,
        title: str = "",
        content: str = "",
        source: str = "",
        category: str = "",
        author: str = "",
        published_date: str = "",
        extra_data: dict | None = None,
        **kwargs
    ) -> int:
        """
        保存爬取记录
        
        如果 URL 已存在，则更新记录
        
        Returns:
            记录 ID
        """
        if extra_data is None:
            extra_data = {}
        
        # 合并额外参数到 extra_data
        extra_data.update(kwargs)
        
        # 提取 source（如果未指定，从 URL 推断）
        if not source:
            source = self._extract_source(url)
        
        if self._session_factory:
            # SQLAlchemy 模式
            with self._get_session() as session:
                # 检查是否存在
                existing = session.query(self._CrawlTable).filter_by(url=url).first()
                
                if existing:
                    # 更新
                    existing.title = title or existing.title
                    existing.content = content or existing.content
                    existing.category = category or existing.category
                    existing.author = author or existing.author
                    existing.published_date = published_date or existing.published_date
                    existing.extra_data = extra_data
                    session.flush()
                    record_id = existing.id
                else:
                    # 新增
                    record = self._CrawlTable(
                        url=url,
                        title=title,
                        content=content,
                        source=source,
                        category=category,
                        author=author,
                        published_date=published_date,
                        extra_data=extra_data
                    )
                    session.add(record)
                    session.flush()
                    record_id = record.id
                
                return record_id
        else:
            # 纯 SQLite 模式
            cursor = self._conn.cursor()
            
            # 检查是否存在
            cursor.execute("SELECT id FROM crawl_records WHERE url = ?", (url,))
            row = cursor.fetchone()
            
            if row:
                # 更新
                cursor.execute("""
                    UPDATE crawl_records 
                    SET title = COALESCE(NULLIF(?, ''), title),
                        content = COALESCE(NULLIF(?, ''), content),
                        category = COALESCE(NULLIF(?, ''), category),
                        author = COALESCE(NULLIF(?, ''), author),
                        published_date = COALESCE(NULLIF(?, ''), published_date),
                        extra_data = ?
                    WHERE url = ?
                """, (title, content, category, author, published_date, 
                      json.dumps(extra_data, ensure_ascii=False), url))
                self._conn.commit()
                return row[0]
            else:
                # 新增
                cursor.execute("""
                    INSERT INTO crawl_records 
                    (url, title, content, source, category, author, published_date, extra_data)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (url, title, content, source, category, author, published_date,
                      json.dumps(extra_data, ensure_ascii=False)))
                self._conn.commit()
                return cursor.lastrowid
    
    def save_batch(self, records: list[dict]) -> int:
        """
        批量保存记录
        
        Args:
            records: 字典列表，每个字典包含爬取数据
            
        Returns:
            保存的记录数
        """
        count = 0
        for record in records:
            self.save_crawl(**record)
            count += 1
        return count
    
    def query(
        self,
        url: str | None = None,
        source: str | None = None,
        category: str | None = None,
        keyword: str | None = None,
        limit: int = 100,
        offset: int = 0
    ) -> list[CrawlRecord]:
        """
        查询记录
        
        Args:
            url: 精确匹配 URL
            source: 匹配来源
            category: 匹配分类
            keyword: 搜索关键词（匹配标题和内容）
            limit: 返回数量限制
            offset: 偏移量
            
        Returns:
            CrawlRecord 列表
        """
        results = []
        
        if self._session_factory:
            with self._get_session() as session:
                query = session.query(self._CrawlTable)
                
                if url:
                    query = query.filter_by(url=url)
                if source:
                    query = query.filter(self._CrawlTable.source.like(f"%{source}%"))
                if category:
                    query = query.filter(self._CrawlTable.category.like(f"%{category}%"))
                if keyword:
                    query = query.filter(
                        self._CrawlTable.title.like(f"%{keyword}%") |
                        self._CrawlTable.content.like(f"%{keyword}%")
                    )
                
                rows = query.order_by(self._CrawlTable.created_at.desc()).limit(limit).offset(offset).all()
                
                for row in rows:
                    results.append(CrawlRecord(
                        id=row.id,
                        url=row.url,
                        title=row.title,
                        content=row.content,
                        source=row.source,
                        category=row.category,
                        author=row.author,
                        published_date=row.published_date,
                        created_at=row.created_at.isoformat() if row.created_at else "",
                        extra_data=row.extra_data or {}
                    ))
        else:
            conditions = []
            params = []
            
            if url:
                conditions.append("url = ?")
                params.append(url)
            if source:
                conditions.append("source LIKE ?")
                params.append(f"%{source}%")
            if category:
                conditions.append("category LIKE ?")
                params.append(f"%{category}%")
            if keyword:
                conditions.append("(title LIKE ? OR content LIKE ?)")
                params.extend([f"%{keyword}%", f"%{keyword}%"])
            
            where = " AND ".join(conditions) if conditions else "1=1"
            
            cursor = self._conn.cursor()
            cursor.execute(f"""
                SELECT * FROM crawl_records 
                WHERE {where}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            """, [*params, limit, offset])
            
            for row in cursor.fetchall():
                results.append(CrawlRecord(
                    id=row[0],
                    url=row[1],
                    title=row[2],
                    content=row[3],
                    source=row[4],
                    category=row[5],
                    author=row[6],
                    published_date=row[7],
                    created_at=row[8],
                    extra_data=json.loads(row[9]) if row[9] else {}
                ))
        
        return results
    
    def count(self, source: str | None = None) -> int:
        """统计记录数"""
        if self._session_factory:
            with self._get_session() as session:
                query = session.query(self._CrawlTable)
                if source:
                    query = query.filter(self._CrawlTable.source.like(f"%{source}%"))
                return query.count()
        else:
            cursor = self._conn.cursor()
            if source:
                cursor.execute("SELECT COUNT(*) FROM crawl_records WHERE source LIKE ?", (f"%{source}%",))
            else:
                cursor.execute("SELECT COUNT(*) FROM crawl_records")
            return cursor.fetchone()[0]
    
    def delete(self, url: str | None = None, source: str | None = None) -> int:
        """删除记录"""
        if not url and not source:
            raise ValueError("必须指定 url 或 source")
        
        if self._session_factory:
            with self._get_session() as session:
                query = session.query(self._CrawlTable)
                if url:
                    query = query.filter_by(url=url)
                if source:
                    query = query.filter(self._CrawlTable.source.like(f"%{source}%"))
                count = query.delete()
                return count
        else:
            cursor = self._conn.cursor()
            if url:
                cursor.execute("DELETE FROM crawl_records WHERE url = ?", (url,))
            elif source:
                cursor.execute("DELETE FROM crawl_records WHERE source LIKE ?", (f"%{source}%",))
            self._conn.commit()
            return cursor.rowcount
    
    def export_to_json(self, output_path: str | Path) -> Path:
        """导出为 JSON 文件"""
        records = self.query(limit=10000)
        data = [r.to_dict() for r in records]
        
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        return output_path
    
    def export_to_csv(self, output_path: str | Path) -> Path:
        """导出为 CSV 文件"""
        records = self.query(limit=10000)
        
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, "w", encoding="utf-8", newline="") as f:
            if records:
                writer = csv.DictWriter(f, fieldnames=records[0].to_dict().keys())
                writer.writeheader()
                for record in records:
                    writer.writerow(record.to_dict())
        
        return output_path
    
    @staticmethod
    def _extract_source(url: str) -> str:
        """从 URL 提取来源标识"""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            # 取域名，去掉 www. 和 .com/.cn 等
            domain = parsed.netloc.replace("www.", "")
            # 去掉顶级域名
            parts = domain.split(".")
            if len(parts) >= 2:
                return parts[-2]  # 如 zhihu from zhihu.com
            return domain
        except:
            return ""
    
    def close(self):
        """关闭连接"""
        if hasattr(self, "_conn"):
            self._conn.close()
        if self._engine:
            self._engine.dispose()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# 默认数据库配置（从环境变量读取）
DEFAULT_DB_TYPE = os.environ.get("CRAWAGENT_DB_TYPE", "sqlite")  # "mysql" 或 "sqlite"
DEFAULT_MYSQL_CONFIG = {
    "host": os.environ.get("CRAWAGENT_MYSQL_HOST", "localhost"),
    "port": int(os.environ.get("CRAWAGENT_MYSQL_PORT", "3306")),
    "user": os.environ.get("CRAWAGENT_MYSQL_USER", "root"),
    "password": os.environ.get("CRAWAGENT_MYSQL_PASSWORD", ""),
    "database": os.environ.get("CRAWAGENT_MYSQL_DATABASE", "crawagent")
}

# 默认数据库实例
_default_db: DatabaseManager | None = None


def get_default_db() -> DatabaseManager:
    """获取默认数据库实例"""
    global _default_db
    if _default_db is None:
        if DEFAULT_DB_TYPE == "mysql":
            _default_db = DatabaseManager(
                db_type="mysql",
                **DEFAULT_MYSQL_CONFIG
            )
        else:
            _default_db = DatabaseManager()
    return _default_db


def save_to_db(
    url: str,
    title: str = "",
    content: str = "",
    source: str = "",
    **kwargs
) -> int:
    """快捷函数：保存到默认数据库"""
    db = get_default_db()
    return db.save_crawl(url=url, title=title, content=content, source=source, **kwargs)
