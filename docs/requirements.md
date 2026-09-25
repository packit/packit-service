# Packit’s requirements for Fedora Messaging Bus messages emitted by Forgejo dist-git

This document is intended for tracking what information we need to be included in the fedmsg messages that we are going to consume from Forgejo dist-git.
This is in relation to the upcoming migration of dist-git from Pagure to Forgejo.

_Fedora Forge_ refers to the existing Forgejo-based instance already emitting fedmsg messages today (which our current Forgejo parsers are modeled on);
_Forgejo dist-git_ refers to the still-unreleased dist-git migration target, whose exact message format we don't yet know.

## Tables of requirements

As of updating this document, Packit’s codebase contains two sets of parsers: parsers for fedmsg events coming from Pagure and equivalent Forgejo parsers.
Our parsers for Forgejo dist-git are currently based on the messages emitted by Fedora Forge.
These parsers will need to be updated once we know the json format and topic names of messages actually emitted by Forgejo dist-git.

The following tables represent what fields we parse from each Pagure event and the equivalent Fedora Forge event.
We will need equivalent fields to be present in the messages coming from Forgejo dist-git.
Fields that are marked as NA in the first column aren't actually required for dist-git support (we parse them only in the Fedora Forge parsers).

Note: The `org.fedoraproject.prod` prefixes are stripped in the topic names (column headers) simply so that the columns render nicely in GitHub's preview of the .md file.

### Push events

| .pagure.git.receive | .forgejo.push                                                                             | Field description                                                       |
| :------------------ | :---------------------------------------------------------------------------------------- | :---------------------------------------------------------------------- |
| .topic              | .topic                                                                                    | Fedora Messaging Bus topic name                                         |
| .repo.namespace     | .body.repository.owner.login                                                              | Repository namespace                                                    |
| .repo.name          | .body.repository.name                                                                     | Repository name                                                         |
| NA\*                | .body.repository.html\_url                                                                | Repository URL                                                          |
| NA                  | .body.deleted                                                                             | Whether this is a deletion of a ref event                               |
| .branch             | .body.ref                                                                                 | Branch of the push event                                                |
| NA                  | .body.before                                                                              | SHA of the previous head commit (before the push)                       |
| .end\_commit        | .body.after                                                                               | SHA of the head commit                                                  |
| .agent              | .body.pusher.login                                                                        | Committer username                                                      |
| .changed\_files     | .body.head\_commit.modified <br> .body.head\_commit.added <br> .body.head\_commit.removed | List of files modified in the push event                                |
| .pull\_request\_id  | \[MISSING\]                                                                               | ID of the pull request <br> (if the push event is associated with a PR) |

\* In the case of Pagure events, the parser derives the URL based on the repo namespace and name.

### Pull request events

| .pagure.pull-request.{new, updated, rebased} | .forgejo.pull\_request                    | Field description                                                                                                       |
| :------------------------------------------- | :---------------------------------------- | :---------------------------------------------------------------------------------------------------------------------- |
| .topic                                       | .topic                                    | Fedora Messaging Bus topic name                                                                                         |
| NA\*                                         | .body.action                              | Specific action that triggered this event (PR opened, synchronized, closed, etc.)                                       |
| .agent                                       | .body.pull\_request.user.login\*\*        | Login of the user who triggered this event (not necessarily the PR author)                                              |
| .pullrequest.id                              | .body.pull\_request.number                | Pull request ID                                                                                                         |
| .pullrequest.project.namespace               | .body.pull\_request.base.repo.owner.login | Namespace of the repository the PR targets (where it merges into)                                                       |
| .pullrequest.project.name                    | .body.pull\_request.base.repo.name        | Name of the repository the PR targets                                                                                   |
| .pullrequest.project.fullname                | .body.pull\_request.base.repo.full_name   | Full name of the repository the PR targets                                                                              |
| .pullrequest.project.full\_url               | .body.repository.html\_url                | URL of the repository the PR targets                                                                                    |
| .pullrequest.repo\_from.name\*\*\*           | .body.pull\_request.head.repo.name        | Name of the repository hosting the PR’s feature branch (the fork, if any)                                               |
| .pullrequest.repo\_from.user.name\*\*\*      | .body.pull\_request.head.repo.owner.login | Owner/namespace of the repository hosting the PR’s feature branch                                                       |
| .pullrequest.repo\_from.full\_url\*\*\*      | NA                                        | URL of the repository hosting the PR’s feature branch <br> (falls back to the target repo’s URL above when not forked). |
| .pullrequest.commit\_stop                    | .body.pull\_request.head.sha              | SHA of the head commit                                                                                                  |
| NA                                           | .body.pull\_request.head.ref              | Feature branch of the PR                                                                                                |
| .pullrequest.branch                          | .body.pull\_request.base.ref              | Target branch of the PR                                                                                                 |
| .pullrequest.merged                          | .body.pull\_request.merged                | Whether the PR is in the merged state                                                                                   |

\* Not applicable, since this information is available directly in the name of the topic.

\*\* It seems `.body.pull_request.user.login` refers to the author of the PR whereas `.body.sender.login` refers to the user who triggered the event
(e.g., who force pushed to the PR). We should probably use the latter.

\*\*\* In the case of Pagure events, `repo_from` is only present when the PR is from a forked repository, and in that case refers to the fork (the repo hosting the PR's feature branch).
`.pullrequest.project` always refers to the target (non-forked) repository the PR was opened against.
When the PR isn't from a fork, `repo_from` is absent: its name/full\_url fall back to `.pullrequest.project`'s (the feature branch lives in the same repo as the target), and its owner falls back to `.agent`.

### Flag / action run events

Pagure flags and Forgejo action runs aren't exactly equivalent events, but close enough:

| .pagure.pull-request.flag      | .forgejo.action\_run\_{success, failure, recover, cancelled} | Field description                                                      |
| :----------------------------- | :----------------------------------------------------------- | :--------------------------------------------------------------------- |
| .topic                         | .topic                                                       | Fedora Messaging Bus topic name                                        |
| NA                             | .body.run.trigger_event                                      | Trigger of this event (push, PR, ...)                                  |
| NA                             | .body.run.prettyref                                          | Branch where the push took place (only in case the trigger was a push) |
| .flag.username                 | .body.run.trigger\_user.login                                | Username of whoever triggered the event                                |
| .flag.comment                  | .body.run.title                                              | Description of the flag / run                                          |
| .flag.status                   | .body.run.status                                             | Status of the flag / run                                               |
| .flag.date\_updated            | .body.run.updated                                            | Datetime of when the flag / run was updated last                       |
| .flag.url                      | .body.run.html\_url                                          | URL of the flag / run                                                  |
| .flag.commit\_hash             | .body.run.commit\_sha                                        | SHA of the commit associated with the given flag / run                 |
| .pullrequest.user.name         | .body.run.event\_payload.pull\_request.user.login\*          | Login of the user who opened the PR associated with the flag / run     |
| .pullrequest.id                | .body.run.event\_payload.pull\_request.number\*              | ID of the PR associated with the flag / run                            |
| .pullrequest.full\_url         | .body.run.event\_payload.pull\_request.url\*                 | URL of the PR associated with the flag / run                           |
| .pullrequest.branch\_from      | .body.run.event\_payload.pull\_request.head.ref\*            | Feature branch of the PR associated with the flag / run                |
| .pullrequest.project.full\_url | .body.run.repository.html\_url                               | URL of the repository where the flag / run was set                     |
| .pullrequest.project.name      | .body.run.repository.name                                    | Name of the repository where the flag / run was set                    |
| .pullrequest.project.namespace | .body.run.repository.owner.login                             | Namespace of the repository where the flag / run was set               |
| .pullrequest.project.fullname  | .body.run.repository.full_name                               | Full name of the repository where the flag / run was set               |

\* In the case of Forgejo events, not present if `.run.trigger\_event` \!= "pull\_request".

### Comment events

Note: Pagure's `pull-request.comment` topic only ever fires for comments on a pull request.
Forgejo's `issue_comment` topic fires for comments on both pull requests _and_ plain issues (Forgejo represents a PR as a special kind of issue), distinguished by `.is_pull`.
That's why several fields below read from `.pull_request.*` when `.is_pull` is true, and fall back to `.repository.*` (there's no PR, so no fork/target-branch concept) when it's a plain issue comment.

| .pagure.pull-request.comment                          | .forgejo.issue\_comment                                                                                  | Field description                                                                                                          |
| :---------------------------------------------------- | :------------------------------------------------------------------------------------------------------- | :------------------------------------------------------------------------------------------------------------------------- |
| .topic                                                | .topic                                                                                                   | Fedora Messaging Bus topic name                                                                                            |
| NA                                                    | .body.action                                                                                             | Specific action that triggered this event (comment created, edited, deleted)                                               |
| NA                                                    | .body.is\_pull                                                                                           | Whether this comment was posted on a pull request or a plain issue (only relevant to Forgejo)                              |
| .pullrequest.id                                       | .body.issue.number                                                                                       | ID of the pull request (issue). Present for plain issue comments too, since Forgejo represents PRs as issues.              |
| .pullrequest.project.namespace                        | PR comments: .body.pull\_request.base.repo.owner.login <br> Issue comments: .body.repository.owner.login | Namespace of the repository the PR targets, or (for issue comments) the repository the issue is in.                        |
| .pullrequest.project.name                             | PR comments: .body.pull\_request.base.repo.name <br> Issue comments: .body.repository.name               | Name of the repository the PR targets, or (for issue comments) the repository the issue is in.                             |
| .pullrequest.project.fullname                         | .body.repository.full_name                                                                               | Full name of repository the comment was posted against (the PR's target repo, or the repo the issue is in).                |
| .pullrequest.project.full\_url                        | .body.repository.html\_url                                                                               | URL of the repository the comment was posted against (the PR's target repo, or the repo the issue is in).                  |
| .pullrequest.repo\_from.user.name\*                   | PR comments: .body.pull\_request.head.repo.owner.login <br> Issue comments: .body.repository.owner.login | Owner/namespace of the repository hosting the PR's feature branch, or (for issue comments) the repository the issue is in. |
| .pullrequest.repo\_from.name\*                        | PR comments: .body.pull\_request.head.repo.name <br> Issue comments: .body.repository.name               | Name of the repository hosting the PR's feature branch, or (for issue comments) the repository the issue is in.            |
| .pullrequest.repo\_from.full\_url\*                   | PR comments: .body.pull\_request.head.repo.html\_url <br> Issue comments: .body.repository.html\_url     | URL of the repository hosting the PR's feature branch, or (for issue comments) URL of the repo the issue is in.            |
| NA                                                    | PR comments: .body.pull\_request.head.ref <br> Issue comments: .body.repository.default\_branch          | Feature branch of the PR. Issue comments have no PR branch, so the repository's default branch is used instead.            |
| .pullrequest.commit\_stop                             | .body.pull\_request.head.sha                                                                             | SHA of the head commit. Only present for PR comments; plain issue comments have no associated commit.                      |
| .pullrequest.comments\[-1\].comment                   | .body.comment.body                                                                                       | Comment body                                                                                                               |
| .pullrequest.comments\[-1\].id                        | .body.comment.id                                                                                         | Comment ID                                                                                                                 |
| .pullrequest.comments\[-1\].user.name <br> .agent\*\* | .body.comment.user.login                                                                                 | Username of the comment author                                                                                             |

\* In the case of Pagure events, `repo_from` is only present when the PR is from a forked repository, and in that case refers to the fork (the repo hosting the PR's feature branch).
`.pullrequest.project` always refers to the target (non-forked) repository the PR was opened against.
When the PR isn't from a fork, `repo_from` is absent: its name/full\_url fall back to `.pullrequest.project`'s (the feature branch lives in the same repo as the target), and its owner falls back to `.agent`.

\*\* In packit-service, we parse `.agent` instead of `.pullrequest.comments[-1].user.name`, but the value should be the same.

## Pagure events in the old schema (for reference)

- List of all events / topics in the current schema: [https://fedora-fedmsg.readthedocs.io/en/latest/topics.html](https://fedora-fedmsg.readthedocs.io/en/latest/topics.html)

The following is a list of all topics Packit is currently listening to and the information we parse from each:

- org.fedoraproject.prod.pagure.git.receive
- org.fedoraproject.prod.pagure.pull-request.new
- org.fedoraproject.prod.pagure.pull-request.updated
- org.fedoraproject.prod.pagure.pull-request.rebased
- org.fedoraproject.prod.pagure.pull-request.comment
- org.fedoraproject.prod.pagure.pull-request.flag

## Fedora Forge events (for reference)

- Relevant documentation:
  - list of Forgejo events / topics in the new schema is located at the bottom of the page (each topic is a link to Datagrepper)
  - [https://docs.fedoraproject.org/en-US/forge-documentation/webhook\_to\_fedora\_messaging/](https://docs.fedoraproject.org/en-US/forge-documentation/webhook_to_fedora_messaging/)
- Summary of events:
  - Doesn’t contain the whole content of the messages, but can be helpful when getting familiar with the topics
  - [https://github.com/fedora-infra/webhook-to-fedora-messaging-messages/blob/main/webhook\_to\_fedora\_messaging\_messages/forgejo/utils.py](https://github.com/fedora-infra/webhook-to-fedora-messaging-messages/blob/main/webhook_to_fedora_messaging_messages/forgejo/utils.py)

## Forgejo dist-git events (for reference)

- Currently unknown
