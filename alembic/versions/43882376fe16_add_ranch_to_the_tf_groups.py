"""add ranch to the TF groups

Revision ID: 43882376fe16
Revises: 48b09a73cf77
Create Date: 2025-05-21 08:34:39.845365

"""

import enum
import logging
from datetime import datetime
from typing import (
    TYPE_CHECKING,
)

import sqlalchemy as sa
from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    orm,
    select,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import (
    relationship,
)

from alembic import op

# https://github.com/python/mypy/issues/2477#issuecomment-313984522 ^_^
if TYPE_CHECKING:
    Base = object
else:
    Base = declarative_base()

# revision identifiers, used by Alembic.
revision = "43882376fe16"
down_revision = "48b09a73cf77"
branch_labels = None
depends_on = None


class TestingFarmResult(str, enum.Enum):
    __test__ = False

    new = "new"
    queued = "queued"
    running = "running"
    passed = "passed"
    failed = "failed"
    skipped = "skipped"
    error = "error"
    unknown = "unknown"
    needs_inspection = "needs_inspection"
    retry = "retry"
    complete = "complete"
    canceled = "canceled"
    cancel_requested = "cancel-requested"

    @classmethod
    def from_string(cls, value):
        try:
            return cls(value)
        except ValueError:
            return cls.unknown


class TFTTestRunGroupModel(Base):
    __tablename__ = "tft_test_run_groups"
    id = Column(Integer, primary_key=True)
    submitted_time = Column(DateTime, default=datetime.utcnow)
    ranch = Column(String)

    # runs = relationship("pipelines", back_populates="test_run_group")
    tft_test_run_targets = relationship(
        "TFTTestRunTargetModel",
        back_populates="group_of_targets",
    )

    @property
    def grouped_targets(self) -> list["TFTTestRunTargetModel"]:
        return self.tft_test_run_targets


class TFTTestRunTargetModel(Base):
    __tablename__ = "tft_test_run_targets"
    id = Column(Integer, primary_key=True)
    pipeline_id = Column(String, index=True)
    identifier = Column(String)
    status = Column(Enum(TestingFarmResult))
    target = Column(String)
    web_url = Column(String)
    # datetime.utcnow instead of datetime.utcnow() because its an argument to the function
    # so it will run when the model is initiated, not when the table is made
    submitted_time = Column(DateTime, default=datetime.utcnow)
    data = Column(JSON)
    tft_test_run_group_id = Column(Integer, ForeignKey("tft_test_run_groups.id"), index=True)

    # copr_builds = relationship(
    #     "copr_build_targets",
    #     secondary="tf_copr_build_association_table",
    #     backref="tft_test_run_targets",
    # )
    # koji_builds = relationship(
    #     "koji_build_targets",
    #     secondary="tf_koji_build_association_table",
    #     backref="tft_test_run_targets",
    # )
    group_of_targets = relationship(
        "TFTTestRunGroupModel",
        back_populates="tft_test_run_targets",
    )


def upgrade():
    bind = op.get_bind()
    session = orm.Session(bind=bind)

    op.add_column("tft_test_run_groups", sa.Column("ranch", sa.String(), nullable=True))

    # Populate the ranches
    targets = select(TFTTestRunTargetModel)
    for (target,) in session.execute(targets):
        if target.web_url is None:
            logging.warning("Empty URL found. Skipping.")
            continue

        ranch = None
        if "testing-farm.io" in target.web_url:
            ranch = "public"
        elif "redhat.com" in target.web_url:
            ranch = "redhat"

        if ranch is None:
            logging.warning("Unknown URL %s found. Skipping.", target.web_url)
        elif target.group_of_targets.ranch and ranch != target.group_of_targets.ranch:
            logging.warning(
                "Found testing group with multiple ranches: %s", target.group_of_targets.id
            )
        elif not target.group_of_targets.ranch:
            target.group_of_targets.ranch = ranch

    session.commit()


def downgrade():
    op.drop_column("tft_test_run_groups", "ranch")
