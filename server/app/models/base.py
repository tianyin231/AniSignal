"""SQLAlchemy declarative base（统一约束命名，保证 Alembic 迁移稳定）。"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=convention)

    def __init_subclass__(cls, **kwargs: object) -> None:
        if "__tablename__" in cls.__dict__:
            table_args = cls.__dict__.get("__table_args__")
            mysql_defaults = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}
            if table_args is None:
                cls.__table_args__ = mysql_defaults
            elif isinstance(table_args, dict):
                cls.__table_args__ = {**mysql_defaults, **table_args}
            elif isinstance(table_args, tuple):
                cls.__table_args__ = (
                    (*table_args[:-1], {**mysql_defaults, **table_args[-1]})
                    if table_args and isinstance(table_args[-1], dict)
                    else (*table_args, mysql_defaults)
                )
        super().__init_subclass__(**kwargs)
