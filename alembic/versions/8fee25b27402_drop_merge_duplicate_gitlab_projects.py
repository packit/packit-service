"""Drop & merge duplicate GitLab projects

Revision ID: 8fee25b27402
Revises: 28beb389d27a
Create Date: 2021-08-26 15:19:49.615046

"""

from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import Column, Integer, String
from sqlalchemy.ext.declarative import declarative_base

from alembic import op

# https://github.com/python/mypy/issues/2477#issuecomment-313984522 ^_^
if TYPE_CHECKING:
    Base = object
else:
    Base = declarative_base()

# revision identifiers, used by Alembic.
revision = "8fee25b27402"
down_revision = "28beb389d27a"
branch_labels = None
depends_on = None


class GitProjectModel(Base):
    __tablename__ = "git_projects"
    id = Column(Integer, primary_key=True)

    project_url = Column(String)
    instance_url = Column(String, nullable=False)


def upgrade():
    bind = op.get_bind()
    session = sa.orm.Session(bind=bind)

    projects = session.query(GitProjectModel).filter(
        GitProjectModel.project_url.like("%.git"),
        GitProjectModel.instance_url == "gitlab.com",
    )
    # project_dot_git = project with the .git suffix which we wanna get rid of
    for project_dot_git in projects:
        # project without the git suffix
        project = (
            session.query(GitProjectModel)
            .filter_by(project_url=project_dot_git.project_url[:-4])
            .first()
        )

        # there is no duplicate project, just rename
        if not project:
            project_dot_git.project_url = project_dot_git.project_url[:-4]
            session.add(project_dot_git)
            continue

        # it is safe to delete when there is no auth issue for the project and there are no PRs
        # sadly `if bool(project.project_auth_issue)` doesn't work as one expects
        if (
            len(project_dot_git.project_authentication_issue) <= 0
            and not project_dot_git.pull_requests
        ):
            session.delete(project_dot_git)
            continue

        # .git project has auth issue and .git-less doesn't, move it
        if (
            len(project.project_authentication_issue) <= 0
            and len(project_dot_git.project_authentication_issue) > 0
        ):
            project_authentication_issue = project_dot_git.project_authentication_issue[0]
            project_authentication_issue.project_id = project.id
            session.add(project_authentication_issue)
            # we need to commit here explicitly b/c we are changing the foreign key
            session.commit()
        elif len(project_dot_git.project_authentication_issue) > 0:
            session.delete(project_dot_git.project_authentication_issue[0])
        session.add(project)
        session.delete(project_dot_git)
    session.commit()


def downgrade():
    # nothing to do here
    pass
