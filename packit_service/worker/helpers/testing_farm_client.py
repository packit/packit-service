# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
import re
from re import Pattern
from typing import Any, Callable, Optional

import requests
from ogr.utils import RequestResponse
from packit.constants import HTTP_REQUEST_TIMEOUT
from packit.exceptions import PackitException

from packit_service.config import ServiceConfig
from packit_service.constants import (
    CONTACTS_URL,
    TESTING_FARM_SUPPORTED_ARCHS,
)

logger = logging.getLogger(__name__)


class TestingFarmClient:
    __test__ = False

    def __init__(self, api_url: str, token: str, use_internal_tf: bool = False) -> None:
        if not api_url.endswith("/"):
            api_url += "/"
        self.api_url = api_url

        self._use_internal_ranch = use_internal_tf
        self._token = token

        self.session = requests.session()
        self.session.mount("https://", requests.adapters.HTTPAdapter(max_retries=5))
        self.session.headers.update({"Authorization": f"Bearer {self._token}"})

    @property
    def default_ranch(self) -> str:
        return "redhat" if self._use_internal_ranch else "public"

    def get_raw_request(
        self,
        url: str,
        method: str = "GET",
        params: Optional[dict] = None,
        data: Optional[dict] = None,
    ) -> RequestResponse:
        response = self.session.request(
            method=method,
            url=url,
            params=params,
            json=data,
            timeout=HTTP_REQUEST_TIMEOUT,
        )

        try:
            json_output = response.json()
        except ValueError:
            logger.debug(response.text)
            json_output = None

        return RequestResponse(
            status_code=response.status_code,
            ok=response.ok,
            content=response.content,
            json=json_output,
            reason=response.reason,
        )

    def send_testing_farm_request(
        self,
        endpoint: str,
        method: str = "GET",
        params: Optional[dict] = None,
        data: Optional[dict] = None,
    ) -> RequestResponse:
        url = f"{self.api_url}{endpoint}"
        try:
            response = self.get_raw_request(
                method=method,
                url=url,
                params=params,
                data=data,
            )
        except requests.exceptions.ConnectionError as err:
            logger.error(err)
            raise PackitException(f"Cannot connect to url: `{url}`") from err
        return response

    def cancel(self, request_id: str) -> bool:
        """
        Cancel TF request with the given ID.

        Args:
            request_id: ID of the TF request.

        Returns:
            Whether the cancelling was successful.
        """
        logger.info(f"Cancelling TF request with ID {request_id}")
        response = self.send_testing_farm_request(
            endpoint=f"requests/{request_id}",
            method="DELETE",
        )
        if response.status_code not in (200, 204):
            # 200: successful test cancellation
            # 204: cancellation has already been requested, or even completed
            msg = f"Failed to cancel TF request {request_id}: {response.json()}"
            logger.error(msg)
            return False

        return True

    @classmethod
    def get_request_details(cls, request_id: str) -> dict[str, Any]:
        """Testing Farm sends only request/pipeline id in a notification.
        We need to get more details ourselves."""
        service_config = ServiceConfig.get_service_config()
        self = cls(
            api_url=service_config.testing_farm_api_url,
            # use the public token, it works for internal TF requests too
            token=service_config.testing_farm_secret,
        )

        response = self.send_testing_farm_request(
            endpoint=f"requests/{request_id}",
            method="GET",
        )
        if response.status_code != 200:
            msg = f"Failed to get request/pipeline {request_id} details from TF. {response.reason}"
            logger.error(msg)
            return {}

        return response.json()
        # logger.debug(f"Request/pipeline {request_id} details: {details}")

    @staticmethod
    def payload_without_token(payload: dict) -> dict:
        """Return a copy of the payload with token/api_key removed."""
        assert "api_key" not in payload, "API key should be passed in header now"

        payload_ = payload.copy()
        # But we still have secret passed by webhook callback for verification
        payload_["notification"]["webhook"].pop("token")
        return payload_

    @property
    def available_composes(self, ranch: Optional[str] = None) -> Optional[set[str]]:
        """
        Fetches available composes from the Testing Farm endpoint.

        Args:
            ranch: Optional parameter that can specify ranch to fetch composes
                of. Available options as of now are: `redhat`, or `public`.

                Defaults to `None` which means that the ranch is deduced from
                the job config.

        Returns:
            Set of all available composes or `None` if error occurs.
        """
        if ranch is None:
            ranch = self.default_ranch

        endpoint = f"composes/{ranch}"

        response = self.send_testing_farm_request(endpoint=endpoint)
        if response.status_code != 200:
            return None

        # {'composes': [{'name': 'CentOS-Stream-8'}, {'name': 'Fedora-Rawhide'}]}
        return {c["name"] for c in response.json()["composes"]}

    @staticmethod
    def is_compose_matching(compose_to_check: str, composes: set[Pattern]) -> bool:
        """
        Check whether the compose matches any compose in the list of re-compiled
        composes.
        """
        return any(compose.fullmatch(compose_to_check) for compose in composes)

    def distro2compose(
        self, distro: str, error_callback: Optional[Callable[[str, Optional[str]], None]] = None
    ) -> Optional[str]:
        """
        Create a compose string from distro, e.g. fedora-33 -> Fedora-33
        https://api.dev.testing-farm.io/v0.1/composes

        The internal TF has a different set and behaves differently:
        * Fedora-3x -> Fedora-3x-Updated
        * CentOS-x ->  CentOS-x-latest

        Returns:
            compose if we were able to map the distro to compose present
            in the list of available composes, otherwise None
        """
        composes = self.available_composes
        if composes is None:
            msg = "We were not able to get the available TF composes."
            logger.error(msg)
            if error_callback:
                error_callback(msg, None)
            return None

        compiled_composes = {re.compile(compose) for compose in composes}

        # if the user precisely specified the compose via target
        # we should just use it instead of continuing below with our logic
        # some of those changes can change the target and result in a failure
        if self.is_compose_matching(distro, compiled_composes):
            logger.debug(
                f"Distro {distro} directly matches a compose in the compose list.",
            )
            return distro

        compose = (
            distro.title()
            .replace("Centos", "CentOS")
            .replace("Rhel", "RHEL")
            .replace("Oraclelinux", "Oracle-Linux")
            .replace("Latest", "latest")
        )
        if compose == "CentOS-Stream":
            compose = "CentOS-Stream-8"

        if self._use_internal_ranch:
            if self.is_compose_matching(compose, compiled_composes):
                return compose

            if compose == "Fedora-Rawhide":
                compose = "Fedora-Rawhide-Nightly"
            elif compose.startswith("Fedora-"):
                compose = f"{compose}-Updated"
            elif compose.startswith("CentOS") and len(compose) == len("CentOS-7"):
                # Attach latest suffix only to major versions:
                # CentOS-7 -> CentOS-7-latest
                # CentOS-8 -> CentOS-8-latest
                # CentOS-8.4 -> CentOS-8.4
                # CentOS-8-latest -> CentOS-8-latest
                # CentOS-Stream-8 -> CentOS-Stream-8
                compose = f"{compose}-latest"
            elif compose == "RHEL-6":
                compose = "RHEL-6-LatestReleased"
            elif compose == "RHEL-7":
                compose = "RHEL-7-LatestReleased"
            elif compose == "RHEL-8":
                compose = "RHEL-8.5.0-Nightly"
            elif compose == "Oracle-Linux-7":
                compose = "Oracle-Linux-7.9"
            elif compose == "Oracle-Linux-8":
                compose = "Oracle-Linux-8.6"

        if not self.is_compose_matching(compose, compiled_composes):
            msg = (
                f"The compose {compose} (from target {distro}) does not match any compose"
                f" in the list of available composes:\n{composes}. "
            )
            logger.debug(msg)
            msg += (
                "Please, check the targets defined in your test job configuration. If you think"
                f" your configuration is correct, get in touch with [us]({CONTACTS_URL})."
            )
            description = (
                f"The compose {compose} is not available in the "
                f"{self.default_ranch} "
                f"Testing Farm infrastructure."
            )
            if error_callback:
                error_callback(description, msg)
            return None

        return compose

    def is_supported_architecture(
        self, arch: str, error_callback: Optional[Callable[[str, Optional[str]], None]] = None
    ) -> bool:
        supported_architectures = TESTING_FARM_SUPPORTED_ARCHS[self.default_ranch]
        if arch not in supported_architectures:
            msg = (
                f"The architecture {arch} is not in the list of "
                f"available architectures:\n{supported_architectures}. "
            )
            logger.debug(msg)
            msg += (
                "Please, check the targets defined in your test job configuration. If you think"
                f" your configuration is correct, get in touch with [us]({CONTACTS_URL})."
            )
            description = (
                f"The architecture {arch} is not available in the "
                f"{self.default_ranch} "
                f"Testing Farm infrastructure."
            )
            if error_callback:
                error_callback(description, msg)
            return False
        return True
