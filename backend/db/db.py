import logging

from sqlmodel import create_engine, SQLModel

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
    RequirementPoint,
    SavedRequest,
    ScheduledTask,
    Session,
    TestCase,
    TestCaseExecutionLog,
    TestPoint,
)

# 创建所有表
def create_db_and_tables():
    SQLModel.metadata.create_all(engine)
    # 自动补齐已存在表中缺失的字段（SQLite 兼容）
    _migrate_missing_columns(engine)
    # 一次性迁移：把旧 requirementpoint.module 文本按模块名回填到 module_id
    _backfill_requirement_point_module_id(engine)


def _try_drop_column(conn, table: str, column: str):
    """尝试删除 SQLite 表中的列（3.35+ 支持），失败则忽略。"""
    from sqlalchemy import text
    try:
        conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))
        logger.info(f"自动迁移: 已删除表 {table} 遗留列 {column}")
    except Exception:
        pass


def _backfill_requirement_point_module_id(engine):
    """一次性迁移：将旧 requirementpoint.module 文本按 (session_id, user_id, 模块名) 匹配回填到 module_id。"""
    import sqlalchemy
    from sqlalchemy import inspect, text

    if engine.dialect.name != "sqlite":
        return

    insp = inspect(engine)
    if "requirementpoint" not in insp.get_table_names():
        return

    cols = {c["name"].lower() for c in insp.get_columns("requirementpoint")}
    # module_id 不存在则等下一轮自动迁移补齐后再处理；module 列已不存在说明迁移完成
    if "module_id" not in cols or "module" not in cols:
        return

    with engine.begin() as conn:
        # 构建 (session_id, user_id, 模块名小写) -> module.id 映射
        module_rows = conn.execute(
            text("SELECT id, module_name, session_id, user_id FROM module")
        ).fetchall()
        name_to_id: dict = {}
        for mid, mname, sid, uid in module_rows:
            key = (sid, uid, (mname or "").strip().lower())
            name_to_id.setdefault(key, mid)

        to_update = conn.execute(
            text(
                "SELECT id, module, session_id, user_id FROM requirementpoint "
                "WHERE module_id IS NULL AND module IS NOT NULL AND module != ''"
            )
        ).fetchall()
        updated = 0
        for rid, mtext, sid, uid in to_update:
            mid = name_to_id.get((sid, uid, (mtext or "").strip().lower()))
            if mid is not None:
                conn.execute(
                    text("UPDATE requirementpoint SET module_id = :mid WHERE id = :rid"),
                    {"mid": mid, "rid": rid},
                )
                updated += 1

        remaining = conn.execute(
            text(
                "SELECT COUNT(*) FROM requirementpoint "
                "WHERE module_id IS NULL AND module IS NOT NULL AND module != ''"
            )
        ).scalar()
        logger.info(
            f"一次性迁移: 将 {updated}/{len(to_update)} 条需求点按模块名回填 module_id"
            + (f"（{remaining} 条未匹配，文本将被丢弃）" if remaining else "")
        )
        # 无条件删除遗留的 module 列：模型已不再使用该字段，
        # 且其 NOT NULL 约束会导致所有新插入失败 (IntegrityError)。
        # 回填已尽力将文本映射到 module_id，未匹配的文本无对应模块记录，保留也无意义。
        _try_drop_column(conn, "requirementpoint", "module")


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

# 数据库会话依赖统一由 app/deps.py 的 get_db（基于本模块的 engine）提供，
# 此处不再重复定义。应用启动时建表/迁移由 main.py 的 lifespan 负责调用。
