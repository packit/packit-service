# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import hmac
import json
import os
from hashlib import sha256
from http import HTTPStatus
from logging import getLogger
from os import getenv

import jwt
from flask import request
from flask_restx import Namespace, Resource, fields
from ogr.parsing import parse_git_repo
from prometheus_client import Counter

from packit_service.celerizer import celery_app
from packit_service.config import ServiceConfig
from packit_service.constants import CELERY_DEFAULT_MAIN_TASK_NAME, GITLAB_ISSUE
from packit_service.models import ProjectAuthenticationIssueModel
from packit_service.service.api.errors import ValidationFailed

logger = getLogger("packit_service")
config = ServiceConfig.get_service_config()

ns = Namespace("webhooks", description="Webhooks")

# Just to be able to specify some payload in Swagger UI
ping_payload = ns.model(
    "Github webhook ping",
    {
        "zen": fields.String(required=False),
        "hook_id": fields.String(required=False),
        "hook": fields.String(required=False),
    },
)

ping_payload_gitlab = ns.model(
    "Gitlab webhook ping",
    {
        "zen": fields.String(required=False),
        "hook_id": fields.String(required=False),
        "hook": fields.String(required=False),
    },
)

github_webhook_calls = Counter(
    "github_webhook_calls",
    "Number of times the GitHub webhook is called",
    # process_id = label the metric with respective process ID, so we can aggregate
    ["result", "process_id"],
)


@ns.route("/github")
class GithubWebhook(Resource):
    @ns.response(HTTPStatus.OK.value, "Webhook accepted, returning reply")
    @ns.response(
        HTTPStatus.ACCEPTED.value,
        "Webhook accepted, request is being processed",
    )
    @ns.response(HTTPStatus.BAD_REQUEST.value, "Bad request data")
    @ns.response(HTTPStatus.UNAUTHORIZED.value, "X-Hub-Signature validation failed")
    # Just to be able to specify some payload in Swagger UI
    @ns.expect(ping_payload)
    def post(self):
        """
        A webhook used by Packit-as-a-Service GitHub App.
        """
        msg = request.json

        if not msg:
            logger.debug("/webhooks/github: we haven't received any JSON data.")
            github_webhook_calls.labels(result="no_data", process_id=os.getpid()).inc()
            return "We haven't received any JSON data.", HTTPStatus.BAD_REQUEST

        if all([msg.get("zen"), msg.get("hook_id"), msg.get("hook")]):
            logger.debug(f"/webhooks/github received ping event: {msg['hook']}")
            github_webhook_calls.labels(result="pong", process_id=os.getpid()).inc()
            return "Pong!", HTTPStatus.OK

        try:
            self.validate_signature()
        except ValidationFailed as exc:
            logger.info(f"/webhooks/github {exc}")
            github_webhook_calls.labels(
                result="invalid_signature",
                process_id=os.getpid(),
            ).inc()
            return str(exc), HTTPStatus.UNAUTHORIZED

        if not self.interested():
            github_webhook_calls.labels(
                result="not_interested",
                process_id=os.getpid(),
            ).inc()
            return "Thanks but we don't care about this event", HTTPStatus.ACCEPTED

        celery_app.send_task(
            name=getenv("CELERY_MAIN_TASK_NAME") or CELERY_DEFAULT_MAIN_TASK_NAME,
            kwargs={
                "event": msg,
                "source": "github",
                "event_type": request.headers.get("X-GitHub-Event"),
            },
        )
        github_webhook_calls.labels(result="accepted", process_id=os.getpid()).inc()

        return "Webhook accepted. We thank you, Github.", HTTPStatus.ACCEPTED

    @staticmethod
    def interested():
        """
        Check whether we want to process this event.

        Returns:
             False if we are not interested in this kind of event
        """
        event = request.headers.get("X-GitHub-Event")
        uuid = request.headers.get("X-GitHub-Delivery")
        action = request.json.get("action")
        deleted = request.json.get("deleted")

        interests = {
            "check_run": action == "rerequested",
            "issue_comment": action in {"created", "edited"},
            "pull_request": action in {"opened", "reopened", "synchronize"},
            "push": not deleted,
            "release": action == "published",
            "installation": action == "created",
            "commit_comment": action == "created",
        }
        _interested = interests.get(event, False)

        logger.debug(f"{event} {uuid}{'' if _interested else ' (not interested)'}")
        return _interested

    @staticmethod
    def validate_signature():
        """
        https://developer.github.com/webhooks/securing/#validating-payloads-from-github
        https://developer.github.com/webhooks/#delivery-headers
        """
        if "X-Hub-Signature-256" not in request.headers:
            if config.validate_webhooks:
                msg = "X-Hub-Signature-256 not in request.headers"
                logger.warning(msg)
                raise ValidationFailed(msg)

            # don't validate signatures when testing locally
            logger.debug("Ain't validating signatures.")
            return

        if not (webhook_secret := config.webhook_secret.encode()):
            msg = "'webhook_secret' not specified in the config."
            logger.error(msg)
            raise ValidationFailed(msg)

        signature = request.headers["X-Hub-Signature-256"].split("=")[1]
        data_hmac = hmac.new(webhook_secret, msg=request.get_data(), digestmod=sha256)
        if not hmac.compare_digest(signature, data_hmac.hexdigest()):
            msg = "Payload signature validation failed."
            logger.warning(msg)
            logger.debug(
                f"X-Hub-Signature-256: {signature!r} != computed: {data_hmac.hexdigest()}",
            )
            raise ValidationFailed(msg)


@ns.route("/gitlab")
class GitlabWebhook(Resource):
    @ns.response(HTTPStatus.OK.value, "Webhook accepted, returning reply")
    @ns.response(
        HTTPStatus.ACCEPTED.value,
        "Webhook accepted, request is being processed",
    )
    @ns.response(HTTPStatus.BAD_REQUEST.value, "Bad request data")
    @ns.response(HTTPStatus.UNAUTHORIZED.value, "X-Gitlab-Token validation failed")
    # Just to be able to specify some payload in Swagger UI
    @ns.expect(ping_payload_gitlab)
    def post(self):
        """
        A webhook used by Packit-as-a-Service Gitlab hook.
        """
        msg = request.json

        if not msg:
            logger.debug("/webhooks/gitlab: we haven't received any JSON data.")
            return "We haven't received any JSON data.", HTTPStatus.BAD_REQUEST

        if all([msg.get("zen"), msg.get("hook_id"), msg.get("hook")]):
            logger.debug(f"/webhooks/gitlab received ping event: {msg['hook']}")
            return "Pong!", HTTPStatus.OK

        try:
            self.validate_token()
        except ValidationFailed as exc:
            logger.info(f"/webhooks/gitlab {exc}")
            return str(exc), HTTPStatus.UNAUTHORIZED

        if not self.interested():
            return "Thanks but we don't care about this event", HTTPStatus.ACCEPTED

        celery_app.send_task(
            name=getenv("CELERY_MAIN_TASK_NAME") or CELERY_DEFAULT_MAIN_TASK_NAME,
            kwargs={
                "event": msg,
                "source": "gitlab",
                "event_type": request.headers.get("X-Gitlab-Event"),
            },
        )

        return "Webhook accepted. We thank you, Gitlab.", HTTPStatus.ACCEPTED

    def create_confidential_issue_with_token(self):
        project_data = request.json["project"]

        http_url = project_data["web_url"]
        parsed_url = parse_git_repo(potential_url=http_url)

        project_authentication_issue = ProjectAuthenticationIssueModel.get_project(
            namespace=parsed_url.namespace,
            repo_name=parsed_url.repo,
            project_url=http_url,
        )

        if not project_authentication_issue:
            token_project = jwt.encode(
                {"namespace": parsed_url.namespace, "repo_name": parsed_url.repo},
                config.gitlab_token_secret,
                algorithm="HS256",
            )
            if isinstance(token_project, bytes):
                token_project = token_project.decode("utf-8")
            token_group = jwt.encode(
                {"namespace": parsed_url.namespace},
                config.gitlab_token_secret,
                algorithm="HS256",
            )
            if isinstance(token_group, bytes):
                token_group = token_group.decode("utf-8")

            project = config.get_project(url=http_url)
            packit_user = project.service.user.get_username()

            project.create_issue(
                title="Packit Authentication",
                body=GITLAB_ISSUE.format(
                    url=project.get_web_url(),
                    token_project=token_project,
                    token_group=token_group,
                    packit_user=packit_user,
                    namespace=parsed_url.namespace,
                    repo=parsed_url.repo,
                ),
                private=True,
            )

            logger.info("Confidential issue created successfully.")

            ProjectAuthenticationIssueModel.create(
                namespace=parsed_url.namespace,
                repo_name=parsed_url.repo,
                project_url=http_url,
                issue_created=True,
            )

    def validate_token(self):
        """
        https://docs.gitlab.com/ee/user/project/integrations/webhooks.html#secret-token
        """
        if "X-Gitlab-Token" not in request.headers:
            if config.validate_webhooks:
                msg = "X-Gitlab-Token not in request.headers"
                logger.info(msg)
                self.create_confidential_issue_with_token()
                raise ValidationFailed(msg)

            # don't validate signatures when testing locally
            logger.debug("Ain't validating token.")
            return

        token = request.headers["X-Gitlab-Token"]

        gitlab_token_secret = config.gitlab_token_secret
        if not gitlab_token_secret:
            msg = "'gitlab_token_secret' not specified in the config."
            logger.error(msg)
            raise ValidationFailed(msg)

        try:
            token_decoded = jwt.decode(
                token,
                config.gitlab_token_secret,
                algorithms=["HS256"],
            )
        except (
            jwt.exceptions.InvalidSignatureError,
            jwt.exceptions.DecodeError,
        ) as exc:
            msg_failed_error = "Can't decode X-Gitlab-Token."
            logger.warning(f"{msg_failed_error} {exc}")
            raise ValidationFailed(msg_failed_error) from exc

        project_data = json.loads(request.data)["project"]
        git_http_url = project_data.get("git_http_url") or project_data["http_url"]
        parsed_url = parse_git_repo(potential_url=git_http_url)

        # "repo_name" might be missing in token_decoded if the token is for group/namespace
        if token_decoded["namespace"] != parsed_url.namespace or (
            "repo_name" in token_decoded and token_decoded["repo_name"] != parsed_url.repo
        ):
            msg_failed_validation = "Decoded X-Gitlab-Token does not match namespace[/project]."
            logger.warning(msg_failed_validation)
            logger.debug(f"decoded: {token_decoded}, url: {parsed_url}")
            raise ValidationFailed(msg_failed_validation)

        logger.debug("Payload signature is OK.")

    @staticmethod
    def interested():
        """
        Check object_kind in request body for events we know we give a f...
        ...finely prepared response to.
        Returns:
             `False` if we are not interested in this kind of event
        """

        interesting_events = {
            "Issue Hook",
            "Merge Request Hook",
            "Note Hook",
            "Pipeline Hook",
            "Push Hook",
            "Release Hook",
        }
        event_type = request.headers.get("X-Gitlab-Event")
        if not event_type and not config.validate_webhooks:
            # probably just a local testing
            logger.debug("X-Gitlab-Event missing, skipping 'interested' check")
            return True
        _interested = event_type in interesting_events

        logger.debug(f"{event_type} {' (not interested)' if not _interested else ''}")
        return _interested
