# MIT License
#
# Copyright (c) 2019 Red Hat, Inc.
#
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
import hmac
import jwt
from hashlib import sha1
from http import HTTPStatus
from logging import getLogger
import json

from flask import request
from prometheus_client import Counter

from ogr.parsing import parse_git_repo

try:
    from flask_restx import Namespace, Resource, fields
except ModuleNotFoundError:
    from flask_restplus import Namespace, Resource, fields

from packit_service.celerizer import celery_app
from packit_service.config import ServiceConfig
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
    "github_webhook_calls", "Number of times the GitHub webhook is called", ["result"]
)


@ns.route("/github")
class GithubWebhook(Resource):
    @ns.response(HTTPStatus.OK.value, "Webhook accepted, returning reply")
    @ns.response(HTTPStatus.ACCEPTED, "Webhook accepted, request is being processed")
    @ns.response(HTTPStatus.BAD_REQUEST, "Bad request data")
    @ns.response(HTTPStatus.UNAUTHORIZED, "X-Hub-Signature validation failed")
    # Just to be able to specify some payload in Swagger UI
    @ns.expect(ping_payload)
    def post(self):
        """
        A webhook used by Packit-as-a-Service GitHub App.
        """
        msg = request.json

        if not msg:
            logger.debug("/webhooks/github: we haven't received any JSON data.")
            github_webhook_calls.labels(result="no_data").inc()
            return "We haven't received any JSON data.", HTTPStatus.BAD_REQUEST

        if all([msg.get("zen"), msg.get("hook_id"), msg.get("hook")]):
            logger.debug(f"/webhooks/github received ping event: {msg['hook']}")
            github_webhook_calls.labels(result="pong").inc()
            return "Pong!", HTTPStatus.OK

        try:
            self.validate_signature()
        except ValidationFailed as exc:
            logger.info(f"/webhooks/github {exc}")
            github_webhook_calls.labels(result="invalid_signature").inc()
            return str(exc), HTTPStatus.UNAUTHORIZED

        if not self.interested():
            github_webhook_calls.labels(result="not_interested").inc()
            return "Thanks but we don't care about this event", HTTPStatus.ACCEPTED

        # TODO: define task names at one place
        celery_app.send_task(
            name="task.steve_jobs.process_message", kwargs={"event": msg}
        )
        github_webhook_calls.labels(result="accepted").inc()

        return "Webhook accepted. We thank you, Github.", HTTPStatus.ACCEPTED

    @staticmethod
    def validate_signature():
        """
        https://developer.github.com/webhooks/securing/#validating-payloads-from-github
        https://developer.github.com/webhooks/#delivery-headers
        """
        if "X-Hub-Signature" not in request.headers:
            if config.validate_webhooks:
                msg = "X-Hub-Signature not in request.headers"
                logger.warning(msg)
                raise ValidationFailed(msg)
            else:
                # don't validate signatures when testing locally
                logger.debug("Ain't validating signatures.")
                return

        sig = request.headers["X-Hub-Signature"]
        if not sig.startswith("sha1="):
            msg = f"Digest mode in X-Hub-Signature {sig!r} is not sha1."
            logger.warning(msg)
            raise ValidationFailed(msg)

        webhook_secret = config.webhook_secret.encode()
        if not webhook_secret:
            msg = "'webhook_secret' not specified in the config."
            logger.error(msg)
            raise ValidationFailed(msg)

        signature = sig.split("=")[1]
        mac = hmac.new(webhook_secret, msg=request.get_data(), digestmod=sha1)
        digest_is_valid = hmac.compare_digest(signature, mac.hexdigest())
        if digest_is_valid:
            logger.debug("Payload signature OK.")
        else:
            msg = "Payload signature validation failed."
            logger.warning(msg)
            logger.debug(f"X-Hub-Signature: {sig!r} != computed: {mac.hexdigest()}")
            raise ValidationFailed(msg)

    @staticmethod
    def interested():
        """
        Check X-GitHub-Event header for events we know we give a f...
        ...finely prepared response to.
        :return: False if we are not interested in this kind of event
        """
        uninteresting_events = {
            "integration_installation",
            "integration_installation_repositories",
        }
        event_type = request.headers.get("X-GitHub-Event")
        uuid = request.headers.get("X-GitHub-Delivery")
        _interested = event_type not in uninteresting_events

        logger.debug(
            f"{event_type} {uuid}{' (not interested)' if not _interested else ''}"
        )
        return _interested


@ns.route("/gitlab")
class GitlabWebhook(Resource):
    @ns.response(HTTPStatus.OK.value, "Webhook accepted, returning reply")
    @ns.response(HTTPStatus.ACCEPTED, "Webhook accepted, request is being processed")
    @ns.response(HTTPStatus.BAD_REQUEST, "Bad request data")
    @ns.response(HTTPStatus.UNAUTHORIZED, "X-Gitlab-Token validation failed")
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

        # TODO: define task names at one place
        celery_app.send_task(
            name="task.steve_jobs.process_message", kwargs={"event": msg}
        )

        return "Webhook accepted. We thank you, Gitlab.", HTTPStatus.ACCEPTED

    def create_confidential_issue_with_token(self):
        project_data = request.json["project"]

        http_url = project_data["git_http_url"]
        parsed_url = parse_git_repo(potential_url=http_url)

        project_authentication_issue = ProjectAuthenticationIssueModel.get_project(
            namespace=parsed_url.namespace,
            repo_name=parsed_url.repo,
            project_url=http_url,
        )

        if not project_authentication_issue:
            token = jwt.encode(
                {"namespace": parsed_url.namespace, "repo_name": parsed_url.repo},
                config.gitlab_token_secret,
                algorithm="HS256",
            ).decode("utf-8")

            project = config.get_project(url=http_url)
            packit_user = project.service.user.get_username()

            project.create_issue(
                title="Packit-Service Authentication",
                body=f"To configure Packit-Service with `{parsed_url.repo}` you need to\n"
                f"configure the webhook settings. Head to {project.get_web_url()}/hooks and add\n"
                f"the Secret Token `{token}` to authenticate requests coming to Packit.\n\n"
                f"Packit needs rights to set commit status to merge requests, Please grant\n"
                f"[{packit_user}](https://gitlab.com/{packit_user}) `admin` permissions\n"
                f"on the {parsed_url.namespace}/{parsed_url.repo} project. "
                f"You can add the rights by clicking\n"
                f"[here]({project.get_web_url()}/-/project_members).",
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
                return
            else:
                # don't validate signatures when testing locally
                logger.debug("Ain't validating token.")
                return

        token = request.headers["X-Gitlab-Token"]

        if token in config.gitlab_webhook_tokens:
            logger.debug("Deprecation Warning: Old Gitlab tokens used.")
            return

        gitlab_token_secret = config.gitlab_token_secret
        if not gitlab_token_secret:
            msg = "'gitlab_token_secret' not specified in the config."
            logger.error(msg)
            raise ValidationFailed(msg)

        try:
            token_decoded = jwt.decode(
                token, config.gitlab_token_secret, algorithms=["HS256"]
            )
        except jwt.exceptions.InvalidSignatureError:
            msg_failed_signature = "Payload token does not match the project."
            logger.warning(msg_failed_signature)
            raise ValidationFailed(msg_failed_signature)
        except jwt.exceptions.DecodeError:
            msg_failed_error = "Payload token has invalid signature."
            logger.warning(msg_failed_error)
            raise ValidationFailed(msg_failed_error)

        project_data = json.loads(request.data)["project"]
        parsed_url = parse_git_repo(potential_url=project_data["http_url"])
        token_namespace = token_decoded["namespace"]
        token_repo_name = token_decoded["repo_name"]

        if (token_namespace, token_repo_name) == (
            parsed_url.namespace,
            parsed_url.repo,
        ):
            logger.debug("Payload signature is OK.")
        else:
            msg_failed_validation = "Signature of the payload token is not valid."
            logger.warning(msg_failed_validation)
            raise ValidationFailed(msg_failed_validation)

    @staticmethod
    def interested():
        """
        Check object_kind in request body for events we know we give a f...
        ...finely prepared response to.
        :return: False if we are not interested in this kind of event
        """

        interesting_events = {
            "Note Hook",
            "Merge Request Hook",
            "Push Hook",
            "Issue Hook",
        }
        event_type = request.headers.get("X-Gitlab-Event")
        _interested = event_type in interesting_events

        logger.debug(f"{event_type} {' (not interested)' if not _interested else ''}")
        return _interested
