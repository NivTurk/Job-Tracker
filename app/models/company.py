from datetime import datetime

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.timeutil import utcnow


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    # Lowercased, punctuation and legal suffixes stripped. Used to match
    # "Check Point Software Technologies Ltd." with "Check Point".
    name_norm: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    is_target: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    applications: Mapped[list["Application"]] = relationship(back_populates="company")  # noqa: F821
