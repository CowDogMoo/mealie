from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, ForeignKey, String, Text, orm
from sqlalchemy.orm import Mapped, mapped_column

from .._filterable_column import FilterableColumn
from .._model_base import BaseMixins, SqlAlchemyBase
from .._model_utils.auto_init import auto_init
from .._model_utils.datetime import NaiveDateTime
from .._model_utils.guid import GUID

if TYPE_CHECKING:
    from ..group import Group
    from .household import Household


class HouseholdCalendarSyncModel(SqlAlchemyBase, BaseMixins):
    """Where one household's meal plan is mirrored to in Google Calendar, and how the last try went.

    One row per household, which the unique index on `household_id` enforces. It holds settings
    and the outcome of the latest sync, and nothing about individual events: which calendar event
    belongs to which plan entry is written on the event itself (a private extended property), so
    a sync can always be worked out again from the plan and the calendar alone.

    The Google credential is not here. It is server-wide, in `GOOGLE_CALENDAR_CREDENTIALS`.
    """

    __tablename__ = "household_calendar_sync"

    id: Mapped[GUID] = mapped_column(GUID, primary_key=True, default=GUID.generate)

    group: Mapped[Optional["Group"]] = orm.relationship("Group", back_populates="calendar_syncs", single_parent=True)
    group_id: Mapped[GUID | None] = mapped_column(GUID, ForeignKey("groups.id"), index=True)
    household: Mapped[Optional["Household"]] = orm.relationship(
        "Household", back_populates="calendar_syncs", single_parent=True
    )
    household_id: Mapped[GUID | None] = mapped_column(GUID, ForeignKey("households.id"), index=True, unique=True)

    calendar_id: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # FilterableColumn rather than Mapped: the hourly sweep asks for every enabled row
    enabled: FilterableColumn[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    last_synced_at: Mapped[datetime | None] = mapped_column(NaiveDateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    @auto_init()
    def __init__(self, **_) -> None: ...
