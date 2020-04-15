import logging
from functools import partial

from packit_service.worker.centos.events import (
    PullRequestCommentPagureEvent,
    PullRequestAction,
    PullRequestPagureEvent,
    PullRequestCommentAction,
    PushPagureEvent,
)

logger = logging.getLogger(__name__)


class CentosEventParser:
    """
    Class responsible for parsing received from CentOS stream
    """

    def __init__(self):
        """
        self.event_mapping: dictionary mapping of topics to corresponding parsing methods
        """
        self.event_mapping = {
            "pull-request.new": partial(self._pull_request_event, action="new"),
            "pull-request.reopened": partial(
                self._pull_request_event, action="reopened"
            ),
            "pull-request.comment.added": partial(
                self._pull_request_comment, action="added"
            ),
            "pull-request.comment.edited": partial(
                self._pull_request_comment, action="edited"
            ),
            "git.receive": self._push_event,
        }

    def parse_event(self, event: dict):
        """
        Entry point for parsing event
        :param event: contains event data
        :return: event object or None
        """

        source, git_topic = event.get("topic").split("/")
        event["source"] = source
        event["git_topic"] = git_topic

        try:
            event_object = self.event_mapping[git_topic](event)
        except KeyError:
            logger.info(f"Event type {git_topic} is not processed")
            return None

        return event_object

    @staticmethod
    def _pull_request_event(event, action):
        logger.debug(f"Parsing pull_request.new")
        pullrequest = event["pullrequest"]

        # "retype" to github equivalents, which are hardcoded in copr build handler
        # needs refactoring
        if action == "new":
            action = "opened"

        pr_id = pullrequest["id"]
        base_repo_namespace = pullrequest["project"]["namespace"]
        base_repo_name = pullrequest["project"]["name"]
        base_ref = f"refs/head/{pullrequest['branch']}"
        target_repo = (pullrequest["repo_from"]["name"],)
        https_url = f"https://{event['source']}/{pullrequest['project']['url_path']}"
        commit_sha = pullrequest["commit_stop"]
        pagure_login = event["agent"]

        return PullRequestPagureEvent(
            PullRequestAction[action],
            pr_id,
            base_repo_namespace,
            base_repo_name,
            base_ref,
            target_repo,
            https_url,
            commit_sha,
            pagure_login,
        )

    def _pull_request_comment(
        self, event: dict, action
    ) -> PullRequestCommentPagureEvent:
        event[
            "https_url"
        ] = f"https://{event['source']}/{event['pullrequest']['project']['url_path']}"
        logger.debug("Parsing pull_request.comment.added")
        action = PullRequestCommentAction.created.value
        pr_id = event["pullrequest"]["id"]
        base_repo_namespace = event["pullrequest"]["project"]["namespace"]
        base_repo_name = event["pullrequest"]["project"]["name"]
        target_repo = event["pullrequest"]["repo_from"]["fullname"]
        https_url = (
            f"https://{event['source']}/{event['pullrequest']['project']['url_path']}"
        )
        pagure_login = event["agent"]

        # gets comment from event.
        # location differs based on topic (pull-request.comment.edited/pull-request.comment.added)
        if "edited" in event["git_topic"]:
            comment = event["comment"]["comment"]
        elif "added" in event["git_topic"]:
            comment = event["pullrequest"]["comments"][-1]["comment"]
        else:
            raise ValueError(
                f"Unknown comment location in response for {event['git_topic']}"
            )

        return PullRequestCommentPagureEvent(
            PullRequestCommentAction[action],
            pr_id,
            base_repo_namespace,
            base_repo_name,
            None,  # the payload does not include this info
            target_repo,
            https_url,
            # todo: change arg name in event class to more general
            pagure_login,
            comment,
        )

    def _push_event(self, event: dict) -> PushPagureEvent:
        logger.debug("Parsing git.receive (git push) event.")

        return PushPagureEvent(
            repo_namespace=event["repo"]["namespace"],
            repo_name=event["repo"]["name"],
            # pagure dont return git_ref, how to handle this?
            git_ref=f"refs/head/{event['branch']}",
            # https_url=event["repo"]["url_path"],
            https_url=f"https://{event['source']}/{event['repo']['url_path']}",
            commit_sha=event["end_commit"],
        )
