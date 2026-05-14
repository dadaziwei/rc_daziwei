from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from notification_service.config import get_settings


class Base(DeclarativeBase):
    pass


engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
