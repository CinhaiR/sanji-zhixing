"""数据库连接与会话"""
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import resolved_database_url

# 统一解析为绝对路径，避免在不同工作目录下启动时创建多份数据库
DATABASE_URL = resolved_database_url()

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# 后加的列 → 「表名.列名: 补列 DDL」。新增列时在此登记即可。
_ADDITIVE_COLUMNS = {
    "itineraries.rationale": (
        "ALTER TABLE itineraries ADD COLUMN rationale TEXT NOT NULL DEFAULT ''"
    ),
    "itineraries.user_id": (
        "ALTER TABLE itineraries ADD COLUMN user_id INTEGER"
    ),
}


def ensure_additive_columns() -> None:
    """SQLite 增量迁移：给已存在的表补上后加的列。

    `Base.metadata.create_all` 只负责建新表，**不会**为已有表新增列，
    所以升级后旧库会报 `no such column`。这里用幂等的 ALTER TABLE 补齐。
    """
    try:
        insp = inspect(engine)
        existing_tables = set(insp.get_table_names())
    except Exception as e:  # pragma: no cover
        print(f"[数据库] 无法检查表结构（已忽略）：{e}")
        return

    for key, ddl in _ADDITIVE_COLUMNS.items():
        table, column = key.split(".", 1)
        if table not in existing_tables:
            continue
        try:
            cols = {c["name"] for c in insp.get_columns(table)}
            if column in cols:
                continue
            with engine.begin() as conn:
                conn.execute(text(ddl))
            print(f"[数据库] 已为 {table} 补列：{column}")
        except Exception as e:  # pragma: no cover
            print(f"[数据库] 为 {table}.{column} 补列失败（已忽略）：{e}")


def get_db():
    """FastAPI 依赖：获取数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
