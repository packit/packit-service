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

# If you have some problems with the imports between files in this directory,
# try using absolute import.
# Example:
# from packit_service.worker.handlers.fedmsg_handlers import something
# instead of
# from packit_service.worker.handlers import something


from packit_service.worker.handlers.abstract import Handler, JobHandler
from packit_service.worker.handlers.comment_action_handler import CommentActionHandler
from packit_service.worker.handlers.fedmsg_handlers import (
    CoprBuildEndHandler,
    CoprBuildStartHandler,
    FedmsgHandler,
    NewDistGitCommitHandler,
)
from packit_service.worker.handlers.github_handlers import (
    AbstractGithubJobHandler,
    GithubAppInstallationHandler,
    GithubCoprBuildHandler,
    GitHubIssueCommentProposeUpdateHandler,
    GitHubPullRequestCommentCoprBuildHandler,
    GitHubPullRequestCommentTestingFarmHandler,
    GithubPullRequestHandler,
    GithubReleaseHandler,
    GithubTestingFarmHandler,
)

from packit_service.worker.handlers.testing_farm_handlers import (
    TestingFarmResultsHandler,
)

__all__ = [
    AbstractGithubJobHandler.__name__,
    CommentActionHandler.__name__,
    CoprBuildEndHandler.__name__,
    CoprBuildStartHandler.__name__,
    FedmsgHandler.__name__,
    GithubAppInstallationHandler.__name__,
    GithubCoprBuildHandler.__name__,
    GitHubIssueCommentProposeUpdateHandler.__name__,
    GitHubPullRequestCommentCoprBuildHandler.__name__,
    GitHubPullRequestCommentTestingFarmHandler.__name__,
    GithubPullRequestHandler.__name__,
    GithubReleaseHandler.__name__,
    GithubTestingFarmHandler.__name__,
    Handler.__name__,
    JobHandler.__name__,
    NewDistGitCommitHandler.__name__,
    TestingFarmResultsHandler.__name__,
]
