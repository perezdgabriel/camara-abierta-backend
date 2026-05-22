from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Column, ForeignKey, SmallInteger, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import SyncableMixin

if TYPE_CHECKING:
    from app.models.legislature import Legislator
    from app.models.proyecto import Bill

circumscription_regions = Table(
    "circumscription_regions",
    Base.metadata,
    Column(
        "circumscription_id",
        ForeignKey("circumscriptions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("region_id", ForeignKey("regions.id", ondelete="CASCADE"), primary_key=True),
)


class Topic(SyncableMixin, Base):
    __tablename__ = "topics"

    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    icon: Mapped[str | None] = mapped_column(String(50))
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("topics.id", ondelete="SET NULL")
    )

    parent: Mapped[Topic | None] = relationship(
        remote_side="Topic.id", back_populates="children"
    )
    children: Mapped[list[Topic]] = relationship(back_populates="parent")
    bills: Mapped[list["Bill"]] = relationship(
        secondary="bill_topics", back_populates="topics"
    )

    def __str__(self) -> str:
        return self.name


class Region(SyncableMixin, Base):
    __tablename__ = "regions"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    number: Mapped[int] = mapped_column(SmallInteger, nullable=False, unique=True)
    capital: Mapped[str] = mapped_column(String(100), nullable=False)

    districts: Mapped[list["District"]] = relationship(back_populates="region")
    provinces: Mapped[list["Province"]] = relationship(back_populates="region")
    communes: Mapped[list["Commune"]] = relationship(back_populates="region")
    circumscriptions: Mapped[list["Circumscription"]] = relationship(
        secondary=circumscription_regions,
        back_populates="regions",
    )

    def __str__(self) -> str:
        return f"Region {self.number} - {self.name}"


class District(SyncableMixin, Base):
    __tablename__ = "districts"

    number: Mapped[int] = mapped_column(SmallInteger, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    region_id: Mapped[int] = mapped_column(
        ForeignKey("regions.id", ondelete="RESTRICT"), nullable=False
    )

    region: Mapped[Region] = relationship(back_populates="districts")
    communes: Mapped[list["Commune"]] = relationship(back_populates="district")
    legislators: Mapped[list["Legislator"]] = relationship(back_populates="district")

    def __str__(self) -> str:
        return f"Distrito {self.number} - {self.name}"


class Province(SyncableMixin, Base):
    __tablename__ = "provinces"

    number: Mapped[int] = mapped_column(SmallInteger, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    region_id: Mapped[int] = mapped_column(
        ForeignKey("regions.id", ondelete="RESTRICT"), nullable=False
    )

    region: Mapped[Region] = relationship(back_populates="provinces")
    communes: Mapped[list["Commune"]] = relationship(back_populates="province")

    def __str__(self) -> str:
        return f"Provincia {self.number} - {self.name}"


class Commune(SyncableMixin, Base):
    __tablename__ = "communes"

    number: Mapped[int] = mapped_column(SmallInteger, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    province_id: Mapped[int | None] = mapped_column(
        ForeignKey("provinces.id", ondelete="RESTRICT")
    )
    region_id: Mapped[int] = mapped_column(
        ForeignKey("regions.id", ondelete="RESTRICT"), nullable=False
    )
    district_id: Mapped[int | None] = mapped_column(
        ForeignKey("districts.id", ondelete="SET NULL")
    )

    province: Mapped[Province | None] = relationship(back_populates="communes")
    region: Mapped[Region] = relationship(back_populates="communes")
    district: Mapped[District | None] = relationship(back_populates="communes")

    def __str__(self) -> str:
        return self.name


class Circumscription(SyncableMixin, Base):
    __tablename__ = "circumscriptions"

    number: Mapped[int] = mapped_column(SmallInteger, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    regions: Mapped[list[Region]] = relationship(
        secondary=circumscription_regions,
        back_populates="circumscriptions",
    )
    legislators: Mapped[list["Legislator"]] = relationship(
        back_populates="circumscription"
    )

    def __str__(self) -> str:
        return f"Circunscripcion {self.number} - {self.name}"
