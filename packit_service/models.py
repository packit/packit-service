# MIT License
#
# Copyright (c) 2018-2019 Red Hat, Inc.

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""
Data layer on top of PSQL using sqlalch
"""
import os
from typing import TYPE_CHECKING, Optional, Union

from sqlalchemy import Column, Integer, String, ForeignKey, Text
from sqlalchemy import JSON, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from sqlalchemy.orm import sessionmaker, Session

# SQLAlchemy session, get it with `get_sa_session`
session_instance = None


def get_pg_url() -> str:
    """ create postgresql connection string """
    return (
        f"postgres+psycopg2://{os.getenv('POSTGRESQL_USER')}"
        f":{os.getenv('POSTGRESQL_PASSWORD')}@{os.getenv('POSTGRES_SERVICE_HOST', 'postgres')}"
        f":{os.getenv('POSTGRESQL_PORT', '5432')}/{os.getenv('POSTGRESQL_DATABASE')}"
    )


def get_sa_session() -> Session:
    """ get SQLAlchemy session """
    global session_instance
    if session_instance is None:
        engine = create_engine(get_pg_url())
        Session = sessionmaker(bind=engine)
        session_instance = Session()
    return session_instance


# https://github.com/python/mypy/issues/2477#issuecomment-313984522 ^_^
if TYPE_CHECKING:
    Base = object
else:
    Base = declarative_base()


class GitProject(Base):
    __tablename__ = "git_projects"
    id = Column(Integer, primary_key=True)
    # github.com/NAMESPACE/REPO_NAME
    # git.centos.org/NAMESPACE/REPO_NAME
    namespace = Column(String, index=True)
    repo_name = Column(String, index=True)
    pull_requests = relationship("PullRequest", back_populates="project")

    @classmethod
    def get_or_create(cls, namespace: str, repo_name: str) -> "GitProject":
        session = get_sa_session()
        project = (
            session.query(GitProject)
            .filter_by(namespace=namespace, repo_name=repo_name)
            .first()
        )
        if not project:
            project = cls()
            project.repo_name = repo_name
            project.namespace = namespace
            session.add(project)
            session.commit()
        return project


class PullRequest(Base):
    __tablename__ = "pull_requests"
    id = Column(Integer, primary_key=True)  # our database PK
    # GitHub PR ID
    # this is not our PK b/c:
    #   1) we don't control it
    #   2) we want sensible auto-incremented ID, not random numbers
    #   3) it's not unique across projects obviously, so why am I even writing this?
    pr_id = Column(Integer, index=True)
    project_id = Column(Integer, ForeignKey("git_projects.id"))
    project = relationship("GitProject", back_populates="pull_requests")
    copr_builds = relationship("CoprBuild", back_populates="pr")

    @classmethod
    def get_or_create(cls, pr_id: int, namespace: str, repo_name: str) -> "PullRequest":
        session = get_sa_session()
        project = GitProject.get_or_create(namespace=namespace, repo_name=repo_name)
        pr = (
            session.query(PullRequest)
            .filter_by(pr_id=pr_id, project_id=project.id)
            .first()
        )
        if not pr:
            pr = PullRequest()
            pr.pr_id = pr_id
            pr.project_id = project.id
            session.add(pr)
            session.commit()
        return pr


class CoprBuild(Base):
    """ we create an entry for every target """

    __tablename__ = "copr_builds"
    id = Column(Integer, primary_key=True)
    build_id = Column(String, index=True)  # copr build id
    pr_id = Column(Integer, ForeignKey("pull_requests.id"))
    pr = relationship("PullRequest", back_populates="copr_builds")
    srpm_build_id = Column(Integer, ForeignKey("srpm_builds.id"))
    srpm_build = relationship("SRPMBuild", back_populates="copr_builds")
    # commit sha of the PR (or a branch, release) we used for a build
    commit_sha = Column(String)
    # what's the build status?
    status = Column(String)
    # chroot, but we use the word target in our docs
    target = Column(String)
    # URL to copr web ui for the particular build
    web_url = Column(String)
    # url to copr build logs
    build_logs_url = Column(String)
    # metadata for the build which didn't make it to schema yet
    # metadata is reserved to sqlalch
    data = Column(JSON)

    def set_status(self, status: str):
        session = get_sa_session()
        self.status = status
        session.add(self)
        session.commit()

    def set_build_logs_url(self, build_logs: str):
        session = get_sa_session()
        self.build_logs_url = build_logs
        session.add(self)
        session.commit()

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["CoprBuild"]:
        session = get_sa_session()
        return session.query(CoprBuild).filter_by(id=id_).first()

    @classmethod
    def get_by_build_id(
        cls, build_id: Union[str, int], target: str
    ) -> Optional["CoprBuild"]:
        if isinstance(build_id, int):
            # PG is pesky about this:
            #   LINE 3: WHERE copr_builds.build_id = 1245767 AND copr_builds.target ...
            #   HINT:  No operator matches the given name and argument type(s).
            #   You might need to add explicit type casts.
            build_id = str(build_id)
        session = get_sa_session()
        return (
            session.query(CoprBuild).filter_by(build_id=build_id, target=target).first()
        )

    @classmethod
    def get_or_create(
        cls,
        pr_id: int,
        build_id: str,
        commit_sha: str,
        repo_name: str,
        namespace: str,
        web_url: str,
        target: str,
        status: str,
        srpm_build: "SRPMBuild",
    ) -> "CoprBuild":
        session = get_sa_session()
        build = cls.get_by_build_id(build_id, target)
        if not build:
            pr = PullRequest.get_or_create(
                pr_id=pr_id, namespace=namespace, repo_name=repo_name
            )
            build = cls()
            build.build_id = build_id
            build.pr_id = pr.id
            build.srpm_build_id = srpm_build.id
            build.status = status
            build.commit_sha = commit_sha
            build.web_url = web_url
            build.target = target
            session.add(build)
            session.commit()
        return build


class SRPMBuild(Base):
    __tablename__ = "srpm_builds"
    id = Column(Integer, primary_key=True)
    # our logs we want to show to the user
    logs = Column(Text)
    copr_builds = relationship("CoprBuild", back_populates="srpm_build")

    @classmethod
    def create(cls, logs: str) -> "SRPMBuild":
        session = get_sa_session()
        srpm_build = cls()
        srpm_build.logs = logs
        session.add(srpm_build)
        session.commit()
        return srpm_build


# coming soon
# class TFTTestRun(Base):
#     __tablename__ = "tft_runs"
#     id = Column(Integer, primary_key=True)
#     pr_id = Column(Integer, ForeignKey("pull_requests.id"))
#     pr = relationship("PullRequest")
#     commit_sha = Column(String)
#     status = Column(String)
#     target = Column(String)
#     data = Column(JSON)
