# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from typing import Any, Optional, Set

from packit.exceptions import PackitException
from packit.utils.koji_helper import KojiHelper
from specfile.utils import NEVR
from packit_service.models import SidetagModel, SidetagGroupModel

logger = logging.getLogger(__name__)


class Sidetag:
    def __init__(self, sidetag: SidetagModel, koji_helper: KojiHelper) -> None:
        self.sidetag = sidetag
        self.koji_helper = koji_helper

    @property
    def sidetag_group(self) -> str:
        return self.sidetag.sidetag_group.name

    @property
    def dist_git_branch(self) -> str:
        return self.sidetag.target

    @property
    def koji_name(self) -> str:
        return self.sidetag.koji_name

    def get_builds(self) -> Set[NEVR]:
        builds = self.koji_helper.get_builds_in_tag(self.koji_name)
        return {NEVR.from_string(b["nvr"]) for b in builds}

    def get_packages(self) -> Set[str]:
        return {b.name for b in self.get_builds()}

    def get_missing_dependencies(self, dependencies: Set[str]) -> Set[str]:
        return dependencies - self.get_packages()

    def get_builds_suitable_for_update(self, dependencies: Set[str]) -> Set[NEVR]:
        builds = self.get_builds()
        result = set()
        for package in dependencies:
            latest_build = max(b for b in builds if b.name == package)
            latest_stable_nvr = self.koji_helper.get_latest_stable_nvr(
                package, self.dist_git_branch, include_candidate=True
            )
            # exclude NVRs that are already in stable or candidate tags - if a build
            # has been manually tagged into the sidetag to satisfy dependencies,
            # we don't want it in the update
            if not latest_stable_nvr or latest_build > NEVR.from_string(
                latest_stable_nvr
            ):
                result.add(latest_build)
        return result

    def tag_build(self, nvr: NEVR) -> None:
        logger.debug(f"Tagging {nvr} into {self.koji_name}")
        self.koji_helper.tag_build(str(nvr), self.koji_name)

    def tag_latest_stable_build(self, package: str) -> None:
        latest_stable_nvr = self.koji_helper.get_latest_stable_nvr(
            package, self.dist_git_branch
        )
        if not latest_stable_nvr:
            logger.debug(f"Failed to find the latest stable build of {package}")
        self.tag_build(NEVR.from_string(latest_stable_nvr))


class SidetagHelperMeta(type):
    def __init__(cls, *args: Any, **kwargs: Any) -> None:
        cls._koji_helper: Optional[KojiHelper] = None

    @property
    def koji_helper(cls) -> KojiHelper:
        if not cls._koji_helper:
            cls._koji_helper = KojiHelper()
        return cls._koji_helper


class SidetagHelper(metaclass=SidetagHelperMeta):
    @classmethod
    def get_sidetag(cls, sidetag_group: str, dist_git_branch: str) -> Sidetag:
        """
        Gets an existing sidetag.

        Args:
            sidetag_group: Sidetag group.
            dist_git_branch: dist-git branch.

        Returns:
            Sidetag object.

        Raises:
            PackitException if the sidetag doesn't exist either in the database or in Koji.
        """
        group = SidetagGroupModel.get_or_create(sidetag_group)
        if not (sidetag := group.get_sidetag_by_target(dist_git_branch)):
            raise PackitException(
                f"Sidetag for {sidetag_group} and {dist_git_branch} was not found in the database"
            )
        if not cls.koji_helper.get_tag_info(sidetag.koji_name):
            raise PackitException(
                f"Sidetag {sidetag.koji_name} no longer exists in Koji"
            )
        return Sidetag(sidetag, cls.koji_helper)

    @classmethod
    def get_sidetag_by_koji_name(cls, koji_name: str) -> Sidetag:
        """
        Gets an existing sidetag by its Koji name.

        Args:
            koji_name: Name of the sidetag in Koji.

        Returns:
            Sidetag object.

        Raises:
            PackitException if the sidetag doesn't exist either in the database or in Koji.
        """
        if not (sidetag := SidetagModel.get_by_koji_name(koji_name)):
            raise PackitException(f"Sidetag {koji_name} was not found in the database")
        if not cls.koji_helper.get_tag_info(sidetag.koji_name):
            raise PackitException(f"Sidetag {koji_name} no longer exists in Koji")
        return Sidetag(sidetag, cls.koji_helper)

    @classmethod
    def get_or_create_sidetag(cls, sidetag_group: str, dist_git_branch: str) -> Sidetag:
        """
        Gets an existing sidetag or creates a new one if it doesn't exist either
        in the database or in Koji.

        Kerberos ticket has to be initialized before calling this method in case
        the sidetag needs to be created in Koji.

        Args:
            sidetag_group: Sidetag group.
            dist_git_branch: dist-git branch.

        Returns:
            Sidetag object.

        Raises:
            PackitException if the sidetag failed to be created in Koji.
        """
        group = SidetagGroupModel.get_or_create(sidetag_group)
        with SidetagModel.get_or_create_for_updating(group, dist_git_branch) as sidetag:
            if not sidetag.koji_name or not cls.koji_helper.get_tag_info(
                sidetag.koji_name
            ):
                tag_info = cls.koji_helper.create_sidetag(dist_git_branch)
                if not tag_info:
                    raise PackitException(
                        f"Failed to create sidetag for {dist_git_branch}"
                    )
                sidetag.koji_name = tag_info["name"]
        return Sidetag(sidetag, cls.koji_helper)
