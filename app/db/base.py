from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base for SQLAlchemy models.

    SQL columns called ``metadata`` must be mapped to another Python attribute
    because ``DeclarativeBase.metadata`` is reserved by SQLAlchemy.
    """
