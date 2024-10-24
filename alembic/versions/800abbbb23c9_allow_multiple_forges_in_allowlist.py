"""Allow multiple forges in allowlist

Revision ID: 800abbbb23c9
Revises: a5c06aa9ef30
Create Date: 2021-03-25 10:43:00.679552

"""

import enum
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import Column, Enum, Integer, String, orm

# from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.declarative import declarative_base

from alembic import op

# revision identifiers, used by Alembic.
revision = "800abbbb23c9"
down_revision = "a5c06aa9ef30"
branch_labels = None
depends_on = None


# https://github.com/python/mypy/issues/2477#issuecomment-313984522 ^_^
if TYPE_CHECKING:
    Base = object
else:
    Base = declarative_base()

ALLOWLIST_CONSTANTS = {
    "approved_automatically": "approved_automatically",
    "waiting": "waiting",
    "approved_manually": "approved_manually",
    "denied": "denied",
}


class AllowlistStatus(str, enum.Enum):
    approved_automatically = ALLOWLIST_CONSTANTS["approved_automatically"]
    waiting = ALLOWLIST_CONSTANTS["waiting"]
    approved_manually = ALLOWLIST_CONSTANTS["approved_manually"]
    denied = ALLOWLIST_CONSTANTS["denied"]


class AllowlistModel(Base):
    __tablename__ = "allowlist"
    id = Column(Integer, primary_key=True)
    namespace = Column(String, index=True)  # renamed from account_name
    status = Column(Enum(AllowlistStatus))
    fas_account = Column(String)

    def to_dict(self) -> dict[str, str]:
        return {
            "namespace": self.namespace,
            "status": self.status,
            "fas_account": self.fas_account,
        }

    def __repr__(self):
        return (
            f'<AllowlistModel(namespace="{self.namespace}", '
            f'status="{self.status}", '
            f'fas_account="{self.fas_account}")>'
        )


def upgrade():
    op.add_column("allowlist", sa.Column("fas_account", sa.String(), nullable=True))

    # rename account_name to namespace
    op.execute("ALTER TABLE allowlist RENAME COLUMN account_name TO namespace")

    # replaces creating new index and dropping old one
    op.execute("ALTER INDEX ix_allowlist_account_name RENAME TO ix_allowlist_namespace")

    # update all of the entries
    bind = op.get_bind()
    session = orm.Session(bind=bind)

    for entry in session.query(AllowlistModel).all():
        if "/" not in entry.namespace:
            entry.namespace = f"github.com/{entry.namespace}"

    session.commit()


def downgrade():
    bind = op.get_bind()
    session = orm.Session(bind=bind)

    for entry in session.query(AllowlistModel).all():
        if entry.namespace.startswith("github.com/"):
            _, entry.namespace = entry.namespace.split("/", 1)
    session.commit()

    # drop additional information
    op.drop_column("allowlist", "fas_account")

    # rename back to account_name
    op.execute("ALTER TABLE allowlist RENAME COLUMN namespace TO account_name")

    # fix index; will it work with just rename? O.o
    op.execute("ALTER INDEX ix_allowlist_namespace RENAME TO ix_allowlist_account_name")
