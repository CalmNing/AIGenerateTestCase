import logging

from sqlmodel import create_engine, SQLModel, Session as SQLSession

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 数据库文件路径
import os

# 获取项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 使用backend/data/testcases.db作为数据库路径
DB_PATH = os.path.join(PROJECT_ROOT, "data/testcases.db")
# 确保data目录存在
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)

# 导入所有模型
from db.models import (
    ApiEndpoint,
    ApiProject,
    ApiScenario,
    ApiScenarioResult,
    GlobalParameter,
    McpServer,
    MockConfig,
    SavedRequest,
    ScheduledTask,
    Session,
    TestCase,
)

# 创建所有表
def create_db_and_tables():
    SQLModel.metadata.create_all(engine)
    # 自动补齐已存在表中缺失的字段（SQLite 兼容）
    _migrate_missing_columns(engine)


def _migrate_missing_columns(engine):
    """检查所有 SQLModel 表定义，为已存在的表自动添加缺失的列（仅 SQLite）。"""
    import sqlalchemy
    from sqlalchemy import inspect, text

    db_type = engine.dialect.name
    if db_type != "sqlite":
        return

    insp = inspect(engine)
    table_names_in_db = insp.get_table_names()

    for table_cls in SQLModel.metadata.sorted_tables:
        if table_cls.name not in table_names_in_db:
            continue  # 表不存在，create_all 已经会处理

        # 获取数据库中已存在的列名（小写比较）
        existing_columns = {col["name"].lower() for col in insp.get_columns(table_cls.name)}

        with engine.begin() as conn:
            for column in table_cls.columns:
                col_name = column.name
                if col_name.lower() in existing_columns:
                    # 列已存在，检查 JSON 列中是否有非法空字符串值并修复
                    from sqlalchemy.dialects.sqlite import JSON as SQLiteJSON
                    if isinstance(column.type, SQLiteJSON):
                        json_default = "{}" if col_name in {"request_schema", "response_schema"} else "[]"
                        try:
                            result = conn.execute(
                                text(f"SELECT id FROM {table_cls.name} WHERE {col_name} = '' OR {col_name} IS NULL LIMIT 1")
                            )
                            if result.fetchone():
                                conn.execute(
                                    text(f"UPDATE {table_cls.name} SET {col_name} = :json_default WHERE {col_name} = '' OR {col_name} IS NULL"),
                                    {"json_default": json_default},
                                )
                                logger.info(f"自动修复: 表 {table_cls.name} 列 {col_name} 中的空字符串/NULL已修正为'{json_default}'")
                        except Exception as e:
                            logger.warning(f"检查表 {table_cls.name} 列 {col_name} 失败: {e}")
                    continue

                # 构建 SQLite 的列定义
                from sqlalchemy.dialects.sqlite import JSON as SQLiteJSON
                is_json_col = isinstance(column.type, SQLiteJSON)
                json_default = "'{}'" if col_name in {"request_schema", "response_schema"} else "'[]'"
                col_type = column.type.compile(dialect=engine.dialect)
                ddl = f"{col_name} {col_type}"
                if not column.nullable:
                    default = column.server_default
                    if default is not None:
                        ddl += f" DEFAULT {default.arg}"
                    else:
                        # SQLite ALTER TABLE ADD COLUMN 要求有默认值（非空列）
                        # JSON 类型列需要有效的 JSON 默认值，不能用空字符串
                        ddl += f" DEFAULT {json_default}" if is_json_col else " DEFAULT ''"
                try:
                    conn.execute(text(f"ALTER TABLE {table_cls.name} ADD COLUMN {ddl}"))
                    logger.info(f"自动迁移: 为表 {table_cls.name} 添加列 {col_name} ({col_type})")
                except Exception as e:
                    logger.warning(f"自动迁移失败: 表 {table_cls.name} 添加列 {col_name} 失败: {e}")

# 获取数据库会话
def get_db():
    db = SQLSession(engine)
    try:
        yield db
    finally:
        db.close()

# 应用启动时调用此函数
create_db_and_tables()
