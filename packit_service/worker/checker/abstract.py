from abc import abstractmethod
from typing import Optional

from packit.config import JobConfig
from packit.config.package_config import PackageConfig

from packit_service.worker.events import EventData
from packit_service.worker.mixin import ConfigMixin, PackitAPIWithDownstreamMixin


class Checker(ConfigMixin, PackitAPIWithDownstreamMixin):
    def __init__(
        self,
        package_config: PackageConfig,
        job_config: JobConfig,
        event: dict,
    ):
        self.package_config = package_config
        self.job_config = job_config
        self.data = EventData.from_event_dict(event)

    @abstractmethod
    def pre_check(self) -> bool:
        ...


class ActorChecker(Checker):
    @property
    def actor(self) -> Optional[str]:
        return self.data.actor

    @abstractmethod
    def _pre_check(self) -> bool:
        ...

    def pre_check(self) -> bool:
        if not self.actor:
            return False
        return self._pre_check()
