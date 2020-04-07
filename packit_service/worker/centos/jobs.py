import logging
from typing import Optional, Dict

import yaml
from ogr import PagureService

from packit.config import parse_loaded_config
from packit_service.worker.centos.centosmsg_handlers import (
    PushPagureCoprBuildHandler,
    CommentHandler,
)
from packit_service.worker.centos.events import (
    Event,
    PagurePullRequestEvent,
    PagurePullRequestCommentEvent,
    PagurePushEvent,
)
from packit_service.worker.centos.parser import CentosEventParser
from packit_service.worker.result import HandlerResults

logger = logging.getLogger(__name__)


class CentosTaskProcessor:
    def __init__(self, config):
        self.get_project_kwargs = dict(
            service_mapping_update={"git.stg.centos.org": PagureService}
        )

        self.event_to_handler_mapping = {
            PagurePullRequestEvent: PushPagureCoprBuildHandler,
            PagurePullRequestCommentEvent: CommentHandler,
            PagurePushEvent: PushPagureCoprBuildHandler,
        }

        self.job_results = dict()
        self.project = None
        self.config = config

    def process_msg(self, event: dict) -> Optional[Dict]:
        """
        Method responsible for processing messages received from CentOS infrastructure

        :param event: message data
        :return task_result: tasks results
        """

        if not event:
            logger.warning("Empty event - nothing to process")
            return None

        logger.debug(f"processing CentOS message")
        event_parser = CentosEventParser()
        event_object = event_parser.parse_event(event)
        logger.debug("Parsing  done")

        if not event_object or not event_object.pre_check():
            return None

        # job_results = self._process_packityaml_jobs_centos(event_object)

        handler = self.event_to_handler_mapping[type(event_object)](
            self.config, event_object
        )
        self.job_results[str(type(event_object))] = handler.run_n_clean()
        task_results = {"jobs": self.job_results, "event": event_object.get_dict()}

        for v in self.job_results.values():
            if not (v and v["success"]):
                logger.warning(task_results)
                logger.error(v["details"]["msg"])
        return task_results

    def _process_packityaml_jobs_centos(
        self, event: Event
    ) -> Dict[str, HandlerResults]:
        """
        .. note:
            pagure api doesn't provide an interface for getting repository content,
            we cannot search for packit config, therefore it is currently hardcoded
            method name should to be improved
        """

        handlers_results = {}
        self.project = event.get_project(get_project_kwargs=self.get_project_kwargs)
        package_config = self.__get_pagure_package_config("packit.yaml")
        job = package_config.jobs[0]

        handler = PushPagureCoprBuildHandler(
            config=self.config,
            job_config=job,
            event=event,
            package_config=package_config,
        )
        handlers_results[job.type.value] = handler.run_n_clean()

        return handlers_results

    # workaround - bad design, doesnt follow SRP, required because
    # different approach will high probably require too much
    # refactoring and probably design tweaks, as it is focused on github
    # and pagure api dont have same capabilities
    def __get_pagure_package_config(self, file_name):
        loaded_config_raw = self.project.get_file_content(file_name)
        loaded_config = yaml.safe_load(loaded_config_raw)
        return parse_loaded_config(loaded_config, spec_file_path="/")
