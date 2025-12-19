import sqlite3
import json
import uuid
import logging
from datetime import datetime
from typing import List, Optional
from backend.db.models import Session, TestCase

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 数据库文件路径
import os
import threading

# 获取项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 使用backend/data/testcases.db作为数据库路径
DB_PATH = os.path.join(PROJECT_ROOT, "data/testcases.db")
# 确保data目录存在
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

class DBManager:
    """数据库管理器，处理SQLite数据库的连接和操作"""
    
    def __init__(self):
        """初始化数据库连接"""
        # 线程锁，保护写操作以避免 sqlite 并发问题
        self._lock = threading.RLock()
        self.conn = None
        self._ensure_db_exists()
    
    def _ensure_db_exists(self):
        """确保数据库文件和表存在"""
        # 创建连接
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        
        # 创建会话表
        self._create_sessions_table()
    
    def _create_sessions_table(self):
        """创建会话表"""
        query = """
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
        """
        self.conn.execute(query)
        self.conn.commit()
    
    def _create_testcases_table(self, session_id: str):
        """为指定会话创建测试用例表"""
        table_name = f"testcases_{session_id}"
        query = f"""
        CREATE TABLE IF NOT EXISTS `{table_name}` (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_name TEXT NOT NULL,
            case_level INTEGER NOT NULL,
            preset_conditions TEXT NOT NULL,
            steps TEXT NOT NULL,
            expected_results TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
        )
        """
        self.conn.execute(query)
        # 确保现有表也有status列
        try:
            self.conn.execute(f"ALTER TABLE `{table_name}` ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'")
        except sqlite3.OperationalError as e:
            # 如果列已存在，忽略错误
            if "duplicate column name" not in str(e).lower():
                raise
        self.conn.commit()
    
    def create_session(self, name: str) -> Session:
        """创建新会话"""
        with self._lock:
            # 生成唯一会话ID
            session_id = str(uuid.uuid4())
            now = datetime.now()

            # 插入会话记录
            query = """
            INSERT INTO sessions (id, name, created_at, updated_at) 
            VALUES (?, ?, ?, ?)
            """
            self.conn.execute(query, (session_id, name, now, now))
            self.conn.commit()

            # 创建测试用例表
            self._create_testcases_table(session_id)

            # 返回会话对象
            return Session(id=session_id, name=name, created_at=now, updated_at=now)
    
    def get_sessions(self) -> List[Session]:
        """获取所有会话"""
        query = "SELECT * FROM sessions ORDER BY created_at DESC"
        cursor = self.conn.execute(query)
        rows = cursor.fetchall()
        
        sessions = []
        for row in rows:
            sessions.append(Session(
                id=row["id"],
                name=row["name"],
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"])
            ))
        
        return sessions
    
    def get_session(self, session_id: str) -> Optional[Session]:
        """获取指定会话"""
        query = "SELECT * FROM sessions WHERE id = ?"
        cursor = self.conn.execute(query, (session_id,))
        row = cursor.fetchone()
        
        if row:
            return Session(
                id=row["id"],
                name=row["name"],
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"])
            )
        return None
    
    def update_session(self, session_id: str, name: str) -> Optional[Session]:
        """更新会话名称"""
        now = datetime.now()
        query = """
        UPDATE sessions SET name = ?, updated_at = ? WHERE id = ?
        """
        result = self.conn.execute(query, (name, now, session_id))
        self.conn.commit()
        
        if result.rowcount > 0:
            return self.get_session(session_id)
        return None
    
    def delete_session(self, session_id: str) -> bool:
        """删除会话及其所有测试用例"""
        logger.info(f"删除会话: {session_id}")
        
        with self._lock:
            try:
                # 开始事务
                self.conn.execute("BEGIN TRANSACTION")
                logger.info("开始删除会话事务")

                # 验证会话是否存在
                session = self.get_session(session_id)
                if not session:
                    logger.error(f"会话不存在: {session_id}")
                    self.conn.rollback()
                    return False

                # 删除会话记录
                query = "DELETE FROM sessions WHERE id = ?"
                result = self.conn.execute(query, (session_id,))
                session_delete_count = result.rowcount
                logger.info(f"删除会话记录成功，影响行数: {session_delete_count}")

                # 删除测试用例表
                table_name = f"testcases_{session_id}"
                query = f"DROP TABLE IF EXISTS `{table_name}`"
                self.conn.execute(query)
                logger.info(f"删除测试用例表成功: {table_name}")

                # 提交事务
                self.conn.commit()
                logger.info(f"会话删除事务提交成功")

                # 验证删除结果
                if session_delete_count > 0:
                    # 再次检查会话是否存在
                    if not self.get_session(session_id):
                        logger.info(f"会话删除验证成功: {session_id}")
                        return True
                    else:
                        logger.error(f"会话删除验证失败: {session_id} 仍然存在")
                        return False
                else:
                    logger.error(f"会话记录删除失败，影响行数: {session_delete_count}")
                    return False

            except sqlite3.Error as e:
                # 回滚事务
                self.conn.rollback()
                logger.error(f"SQLite错误: {e}")
                return False

            except Exception as e:
                # 回滚事务
                self.conn.rollback()
                logger.error(f"删除会话失败: {e}")
                import traceback
                traceback.print_exc()
                return False
    
    def save_testcase(self, session_id: str, testcase: TestCase) -> TestCase:
        """保存测试用例到指定会话"""
        table_name = f"testcases_{session_id}"
        
        logger.info(f"保存测试用例: {testcase.case_name} 到会话: {session_id}")
        
        with self._lock:
            try:
                # 确保测试用例表存在
                self._create_testcases_table(session_id)

                # 准备数据
                data = {
                    "case_name": testcase.case_name,
                    "case_level": testcase.case_level,
                    "preset_conditions": json.dumps(testcase.preset_conditions),
                    "steps": json.dumps(testcase.steps),
                    "expected_results": json.dumps(testcase.expected_results),
                    "created_at": testcase.created_at.isoformat(),
                    "updated_at": testcase.updated_at.isoformat(),
                    "status": testcase.status
                }

                logger.info(f"测试用例数据: {data}")

                # 开始事务
                self.conn.execute("BEGIN TRANSACTION")

                if testcase.id is None:
                    # 插入新测试用例
                    query = f"""
                    INSERT INTO `{table_name}` (
                        case_name, case_level, preset_conditions, 
                        steps, expected_results, created_at, updated_at,status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """
                    cursor = self.conn.execute(query, (
                        data["case_name"], data["case_level"], data["preset_conditions"],
                        data["steps"], data["expected_results"], data["created_at"], data["updated_at"],data["status"]
                    ))
                    testcase.id = cursor.lastrowid
                    logger.info(f"插入新测试用例成功，ID: {testcase.id}")
                else:
                    # 更新现有测试用例
                    query = f"""
                    UPDATE `{table_name}` SET 
                        case_name = ?, case_level = ?, preset_conditions = ?, 
                        steps = ?, expected_results = ?, created_at = ?, updated_at = ?,status = ?
                    WHERE id = ?
                    """
                    result = self.conn.execute(query, (
                        data["case_name"], data["case_level"], data["preset_conditions"],
                        data["steps"], data["expected_results"], data["created_at"],data["updated_at"],  data["status"],
                        testcase.id
                    ))
                    logger.info(f"更新测试用例成功，影响行数: {result.rowcount}")

                # 更新会话的更新时间
                now = datetime.now()
                query = "UPDATE sessions SET updated_at = ? WHERE id = ?"
                result = self.conn.execute(query, (now.isoformat(), session_id))
                logger.info(f"更新会话时间成功，影响行数: {result.rowcount}")

                # 提交事务
                self.conn.commit()
                logger.info(f"事务提交成功")

            except Exception as e:
                # 回滚事务
                self.conn.rollback()
                logger.error(f"保存测试用例失败: {e}")
                raise
        
        return testcase
    
    def save_testcases(self, session_id: str, testcases: List[TestCase]) -> List[TestCase]:
        """批量保存测试用例"""
        logger.info(f"批量保存测试用例，数量: {len(testcases)} 到会话: {session_id}")
        saved_testcases = []
        with self._lock:
            try:
                for i, testcase in enumerate(testcases):
                    logger.info(f"保存第 {i+1}/{len(testcases)} 个测试用例")
                    testcase.created_at = datetime.now()
                    testcase.updated_at = datetime.now()
                    saved_testcase = self.save_testcase(session_id, testcase)
                    saved_testcases.append(saved_testcase)
                logger.info(f"批量保存测试用例成功，保存数量: {len(saved_testcases)}")
            except Exception as e:
                logger.error(f"批量保存测试用例失败: {e}")
                raise
        return saved_testcases
    
    def get_testcases(self, session_id: str) -> List[TestCase]:
        """获取指定会话的所有测试用例"""
        # 确保测试用例表存在
        self._create_testcases_table(session_id)
        
        table_name = f"testcases_{session_id}"
        query = f"SELECT * FROM `{table_name}` ORDER BY id"
        cursor = self.conn.execute(query)
        rows = cursor.fetchall()
        
        testcases = []
        for row in rows:
            testcases.append(TestCase(
                id=row["id"],
                case_name=row["case_name"],
                case_level=row["case_level"],
                preset_conditions=json.loads(row["preset_conditions"]),
                steps=json.loads(row["steps"]),
                expected_results=json.loads(row["expected_results"]),
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
                session_id=session_id,
                status=row["status"]
            ))
        
        return testcases
    
    def delete_testcase(self, session_id: str, testcase_id: int) -> bool:
        """删除指定测试用例"""
        table_name = f"testcases_{session_id}"
        query = f"DELETE FROM `{table_name}` WHERE id = ?"
        with self._lock:
            result = self.conn.execute(query, (testcase_id,))
            self.conn.commit()
            return result.rowcount > 0
    
    def close(self):
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()

# 创建全局数据库管理器实例
db_manager = DBManager()
