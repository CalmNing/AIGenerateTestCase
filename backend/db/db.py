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
from db.models import Session, TestCase

# 创建所有表
def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

# 应用启动时调用此函数
create_db_and_tables()