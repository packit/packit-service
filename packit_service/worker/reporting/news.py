# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from random import choice
from typing import ClassVar


class News:
    __FOOTERS: ClassVar[list[str]] = [
        "Do you maintain a Fedora package and don't have access to the upstream repository? "
        "Packit can help. "
        "Take a look [here](https://packit.dev/posts/pull-from-upstream/) to know more.",
        "Do you maintain a Fedora package and you think it's boring? Packit can help. "
        "Take a look [here](https://packit.dev/posts/downstream-automation/) to know more.",
        "Want to use a build from a different project when testing? "
        "Take a look [here](https://packit.dev/posts/testing-farm-triggering/) to know more.",
        "Curious how Packit handles the Release field during propose-downstream? "
        "Take a look [here](https://packit.dev/posts/release-field-handling/) to know more.",
        "Did you know Packit is on Mastodon? Or, more specifically, on Fosstodon? "
        "Follow [@packit@fosstodon.org](https://fosstodon.org/@packit) "
        "and be one of the first to know about all the news!",
        "Interested in the Packit team plans and priorities? "
        "Check [our epic board](https://github.com/orgs/packit/projects/7/views/29).",
        "Want to catch security issues early? [Read more](https://packit.dev/posts/openscanhub-prototype)"
        " about our SAST integration!",
    ]

    @classmethod
    def get_sentence(cls) -> str:
        """
        A random sentence to show our users as a footer when adding a status.
        (Will be visible at the very bottom of the markdown field.
        """
        return choice(cls.__FOOTERS)


class DistgitAnnouncement:
    """
    Class for handling dist-git announcements that are placed e.g. as a PR
    description footer or a dist-git comment footer for comments from
    packit-service.
    """

    __ANNOUNCEMENT = None

    @classmethod
    def get_announcement(cls):
        return cls.__ANNOUNCEMENT

    @classmethod
    def get_comment_footer_with_announcement_if_present(cls):
        announcement = cls.get_announcement()
        return f"\n\n---\n\n{announcement}" if announcement else ""
