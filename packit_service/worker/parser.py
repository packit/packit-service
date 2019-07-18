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
Parser is transforming github JSONs into `events` objects
"""
import logging
from typing import Optional, Union

from packit.utils import nested_get

from packit_service.service.events import (
    PullRequestEvent,
    InstallationEvent,
    ReleaseEvent,
    DistGitEvent,
)
from packit_service.worker.fedmsg_handlers import NewDistGitCommit

logger = logging.getLogger(__name__)


class Parser:
    @staticmethod
    def parse_event(
        event: dict
    ) -> Optional[
        Union[PullRequestEvent, InstallationEvent, ReleaseEvent, DistGitEvent]
    ]:
        """
        Try to parse all JSONs that we process
        :param event: JSON from Github or fedmsg
        :return: event object
        """

        if not event:
            logger.warning("No event to process!")
            return None

        response: Optional[
            Union[PullRequestEvent, InstallationEvent, ReleaseEvent, DistGitEvent]
        ] = Parser.parse_pr_event(event)
        if response:
            return response

        response = Parser.parse_release_event(event)
        if response:
            return response

        response = Parser.parse_installation_event(event)
        if response:
            return response

        response = Parser.parse_distgit_event(event)
        if response:
            return response

        return response

    @staticmethod
    def parse_pr_event(event) -> Optional[PullRequestEvent]:
        """ look into the provided event and see if it's one for a new github pr """
        action = nested_get(event, "action")
        logger.debug(f"action = {action}")
        pr_id = nested_get(event, "number")
        is_pr = nested_get(event, "pull_request")
        if not is_pr:
            logger.info("Not a pull request event.")
            return None
        if action in ["opened", "reopened", "synchronize"] and pr_id:
            # we can't use head repo here b/c the app is set up against the upstream repo
            # and not the fork, on the other hand, we don't process packit.yaml from
            # the PR but what's in the upstream
            base_repo_namespace = nested_get(
                event, "pull_request", "base", "repo", "owner", "login"
            )
            base_repo_name = nested_get(event, "pull_request", "base", "repo", "name")

            if not (base_repo_name and base_repo_namespace):
                logger.warning(
                    "We could not figure out the full name of the repository."
                )
                return None
            base_ref = nested_get(event, "pull_request", "head", "sha")
            if not base_ref:
                logger.warning("Ref where the PR is coming from is not set.")
                return None
            target_repo = nested_get(event, "repository", "full_name")
            logger.info(f"GitHub pull request {pr_id} event for repo {target_repo}.")

            commit_sha = nested_get(event, "pull_request", "head", "sha")
            https_url = event["repository"]["html_url"]
            return PullRequestEvent(
                action,
                pr_id,
                base_repo_namespace,
                base_repo_name,
                base_ref,
                target_repo,
                https_url,
                commit_sha,
            )
        return None

    @staticmethod
    def parse_installation_event(event) -> Optional[InstallationEvent]:
        """ look into the provided event and see github app installation details """
        action = nested_get(event, "action")  # created or deleted
        logger.debug(f"action = {action}")

        if not event.get("installation", None):
            return None

        # it is not enough to check if installation key in JSON, we have to check the account
        if not event["installation"].get("account", None):
            return None

        installation_id = event["installation"]["id"]
        account_login = event["installation"]["account"]["login"]
        account_id = event["installation"]["account"]["id"]
        account_url = event["installation"]["account"]["url"]
        account_type = event["installation"]["account"]["type"]  # User or Organization
        created_at = event["installation"]["created_at"]

        sender_id = event["sender"]["id"]
        sender_login = event["sender"]["login"]

        return InstallationEvent(
            installation_id,
            account_login,
            account_id,
            account_url,
            account_type,
            created_at,
            sender_id,
            sender_login,
        )

    @staticmethod
    def parse_release_event(event) -> Optional[ReleaseEvent]:
        """
        look into the provided event and see if it's one for a published github release;
        if it is, process it and return input for the job handler
        """
        action = nested_get(event, "action")
        logger.debug(f"action = {action}")
        release = nested_get(event, "release")
        if action == "published" and release:
            repo_namespace = nested_get(event, "repository", "owner", "login")
            repo_name = nested_get(event, "repository", "name")
            if not (repo_namespace and repo_name):
                logger.warning(
                    "We could not figure out the full name of the repository."
                )
                return None
            release_ref = nested_get(event, "release", "tag_name")
            if not release_ref:
                logger.warning("Release tag name is not set.")
                return None
            logger.info(
                f"New release event {release_ref} for repo {repo_namespace}/{repo_name}."
            )
            https_url = event["repository"]["html_url"]
            return ReleaseEvent(repo_namespace, repo_name, release_ref, https_url)
        return None

    @staticmethod
    def parse_distgit_event(event) -> Optional[DistGitEvent]:
        """ this corresponds to dist-git event when someone pushes new commits """
        topic = nested_get(event, "topic")
        logger.debug(f"topic = {topic}")
        if topic == NewDistGitCommit.topic:
            repo_namespace = nested_get(event, "msg", "commit", "namespace")
            repo_name = nested_get(event, "msg", "commit", "repo")
            ref = nested_get(event, "msg", "commit", "branch")
            if not (repo_namespace and repo_name):
                logger.warning(
                    "We could not figure out the full name of the repository."
                )
                return None
            if not ref:
                logger.warning("Target branch for the new commits is not set.")
                return None
            logger.info(
                f"New commits added to dist-git repo {repo_namespace}/{repo_name}, branch {ref}."
            )
            msg_id = nested_get(event, "msg_id")
            logger.info(f"msg_id = {msg_id}")

            branch = nested_get(event, "msg", "commit", "branch")
            return DistGitEvent(topic, repo_namespace, repo_name, ref, branch, msg_id)
        return None
