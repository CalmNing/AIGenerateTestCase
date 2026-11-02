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
    MockProxyRule,
    NavCategory,
    NavLink,
    RequirementPoint,
    SavedRequest,
    SavedRequestFolder,
    ScheduledTask,
    Session,
    TestCase,
    TestCaseExecutionLog,
    TestPoint,
)

# 创建所有表
def create_db_and_tables():
    # 先修复历史遗留的错误表结构（早于当前模型建出的表），再建缺失的表
    _drop_stale_pushlog_schema(engine)
    SQLModel.metadata.create_all(engine)
    # 自动补齐已存在表中缺失的字段（SQLite 兼容）
    _migrate_missing_columns(engine)
    # 一次性迁移：把旧 requirementpoint.module 文本按模块名回填到 module_id
    _backfill_requirement_point_module_id(engine)
    # 一次性迁移：按规则路径模式回填服务前缀，让存量规则立即按真实流量匹配
    _backfill_mock_proxy_service_prefix(engine)
    # 一次性清理：登录态改为挂在页面对象上，旧的独立「登录配置」结构作废
    _drop_obsolete_login_profile_schema(engine)
    # P2：重建 FTS5 检索索引（存量历史需求首次进入检索层）
    _rebuild_retrieval_index(engine)


def _drop_stale_pushlog_schema(engine):
    """pushlog 表若仍是早期错误结构（original_* / response_status_code），整表重建。

    早期版本的列名与当前 PushLog 模型不一致，且旧列为 NOT NULL 无默认值；
    _migrate_missing_columns 只会补新列、无法删除旧列，导致 INSERT 永远失败
    （NOT NULL constraint failed: pushlog.original_url）。表为空时直接丢弃重建。
    """
    from sqlalchemy import inspect, text

    try:
        insp = inspect(engine)
        if "pushlog" not in insp.get_table_names():
            return
        cols = {c["name"] for c in insp.get_columns("pushlog")}
        # 结构一致（已用当前列名）则不动
        if "source_url" in cols and "original_url" not in cols:
            return
        if "original_url" not in cols and "source_url" not in cols:
            return
        with engine.begin() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM pushlog")).scalar() or 0
            if count:
                logger.warning("自动迁移: pushlog 旧结构仍有 %s 条数据，跳过重建", count)
                return
            conn.execute(text("DROP TABLE IF EXISTS pushlog"))
            logger.info("自动迁移: 已丢弃旧结构 pushlog 表（original_* 列），将按当前模型重建")
    except Exception as e:  # noqa: BLE001 - 修复失败不阻断启动
        logger.warning(f"自动迁移: 检查/重建 pushlog 失败(忽略): {e}")


def _drop_obsolete_login_profile_schema(engine):
    """清理作废的登录配置结构（pageobject.login_profile_id 列 + login_profile 表）。

    登录态已改为挂在页面对象自身（is_login_page / login_page_object_id / session_*），
    独立的 LoginProfile 实体不再使用。幂等且失败忽略，不影响启动。
    """
    from sqlalchemy import text
    try:
        with engine.begin() as conn:
            _try_drop_column(conn, "pageobject", "login_profile_id")
            conn.execute(text("DROP TABLE IF EXISTS login_profile"))
    except Exception as e:  # noqa: BLE001 - 清理失败不该阻断启动
        logger.warning(f"自动迁移: 清理登录配置遗留结构失败(忽略): {e}")


def _rebuild_retrieval_index(engine):
    """把 historyprompt 全量同步进 FTS5 检索索引（P2）。

    索引是缓存，源表 historyprompt 才是唯一事实来源；
    启动时全量重建一次，保证存量历史需求立即可被检索召回。
    任何失败都只告警，不阻断启动。
    """
    try:
        from sqlalchemy import text

        with engine.begin() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, content, module_id, user_id, session_id, created_at "
                    "FROM historyprompt"
                )
            ).fetchall()
        if not rows:
            return
        source = [
            {
                "id": r[0],
                "content": r[1],
                "module_id": r[2],
                "user_id": r[3],
                "session_id": r[4],
                "created_at": r[5],
            }
            for r in rows
        ]
        from utils.retrieval import get_index

        idx = get_index()
        if idx is None:
            logger.warning("检索索引不可用（FTS5 未启用），历史召回将降级为时间序")
            return
        # 增量写入已经保证索引跟上源表；只有条目数不一致（首次启动/索引丢失）时才全量重建，
        # 避免每次启动都做 O(N) 的全量清空重建。
        if idx.indexed_count() == len(source):
            logger.debug("检索索引已是最新（%d 条），跳过重建", len(source))
            return
        count = idx.sync_from_source(source)
        if count:
            logger.info("已重建检索索引：%d 条历史需求", count)
    except Exception as exc:  # noqa: BLE001 - 索引重建失败不该阻断启动
        logger.warning("重建检索索引失败（已忽略，检索降级为时间序）: %s", exc)


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

def _backfill_mock_proxy_service_prefix(engine):
    """一次性迁移：为存量转发规则按 path_pattern 回填 service_prefix。

    历史规则写的是 /contract/ 或 /service/contract/*，而平台 Mock 的 url_path 是
    服务内相对路径（/update）。回填服务前缀后，代理才能把真实流量剥离前缀再匹配。
    空串或纯 * 的规则保持为空（不剥离），幂等且失败忽略。
    """
    import sqlalchemy
    from sqlalchemy import inspect, text

    if engine.dialect.name != "sqlite":
        return

    insp = inspect(engine)
    if "mockproxyrule" not in insp.get_table_names():
        return
    cols = {c["name"].lower() for c in insp.get_columns("mockproxyrule")}
    if "service_prefix" not in cols:
        return  # 等下一轮自动迁移补齐列后再处理

    from app.services.mock_matcher import rule_fixed_prefix

    try:
        with engine.begin() as conn:
            rows = conn.execute(
                text("SELECT id, path_pattern, service_prefix FROM mockproxyrule")
            ).fetchall()
            updated = 0
            for rule_id, path_pattern, service_prefix in rows:
                if (service_prefix or "").strip():
                    continue  # 用户显式配置过，不覆盖
                prefix = rule_fixed_prefix(path_pattern or "")
                if not prefix:
                    continue
                conn.execute(
                    text("UPDATE mockproxyrule SET service_prefix = :p WHERE id = :i"),
                    {"p": prefix, "i": rule_id},
                )
                updated += 1
            if updated:
                logger.info(f"一次性迁移: 为 {updated} 条 Mock 转发规则按路径模式回填服务前缀")
    except Exception as e:  # noqa: BLE001 - 回填失败不阻断启动
        logger.warning(f"自动迁移: 回填 Mock 转发规则服务前缀失败(忽略): {e}")
# 数据库会话依赖统一由 app/deps.py 的 get_db（基于本模块的 engine）提供，
# 此处不再重复定义。应用启动时建表/迁移由 main.py 的 lifespan 负责调用。
