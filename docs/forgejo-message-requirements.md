# Packit's Requirements for Forgejo Dist-Git Messages

This document lists the message formats and topic names that Packit
requires from the Fedora Forge (Forgejo dist-git) messaging
infrastructure. It is intended to be shared with the Fedora Forge team
so they can verify that the messages emitted by their system satisfy
Packit's parsing requirements.

Related: [epic #2861](https://github.com/packit/packit-service/issues/2861),
[event parsing #2862](https://github.com/packit/packit-service/issues/2862).

## General Message Envelope

All messages arrive via Fedora Messaging. Each message has the
following top-level structure:

```json
{
  "body": { ... },
  "topic": "org.fedoraproject.prod.forgejo.<event_type>"
}
```

Packit's parser (`packit_service.worker.parser.Parser`) extracts
`topic` to route the message to the correct handler and `body` to
extract the Forgejo webhook payload. The `topic` field must contain a
substring matching one of the topic keys listed below.

## Topic Names

Packit matches topics by substring (using `in` checks) against the
following keys. The full Fedora Messaging topic includes the
`org.fedoraproject.prod.` (or `.stg.`) prefix.

| Topic substring                | Event type           | Parser method                    |
| ------------------------------ | -------------------- | -------------------------------- |
| `forgejo.push`                 | Push / branch commit | `parse_forgejo_push_event`       |
| `forgejo.pull_request`         | Pull request action  | `parse_forgejo_pr_event`         |
| `forgejo.issue_comment`        | Issue or PR comment  | `parse_forgejo_comment_event`    |
| `forgejo.action_run_success`   | Action run completed | `parse_forgejo_action_run_event` |
| `forgejo.action_run_failure`   | Action run failed    | `parse_forgejo_action_run_event` |
| `forgejo.action_run_recover`   | Action run recovered | `parse_forgejo_action_run_event` |
| `forgejo.action_run_cancelled` | Action run cancelled | `parse_forgejo_action_run_event` |

## Event Types and Required Fields

For each event type below, the "Required" column indicates whether
Packit's parser treats the field as mandatory (the event is skipped
or parsing fails when it is missing). Fields marked "Optional" are
read when present but have fallback behavior.

All field paths are relative to `body` (the Forgejo webhook payload)
unless stated otherwise.

---

### 1. Push Event

**Topic:** `org.fedoraproject.prod.forgejo.push`

Triggered when commits are pushed to a branch or tag.

#### Required fields

| Field path               | Type   | Description                              |
| ------------------------ | ------ | ---------------------------------------- |
| `ref`                    | string | Full git ref (e.g. `refs/heads/main`)    |
| `before`                 | string | Commit SHA before the push               |
| `after`                  | string | Commit SHA after the push (new HEAD)     |
| `pusher.login`           | string | Username of the person who pushed        |
| `repository.owner.login` | string | Repository namespace / owner login       |
| `repository.name`        | string | Repository name                          |
| `repository.html_url`    | string | Repository URL (used as the project URL) |

#### Optional fields

| Field path      | Type | Description                                          |
| --------------- | ---- | ---------------------------------------------------- |
| `deleted`       | bool | If `true`, the push deletes the ref; Packit skips it |
| `total_commits` | int  | Number of commits in the push (used for logging)     |

#### Ref format

The `ref` field must follow the `refs/<type>/<name>` format (e.g.
`refs/heads/main`, `refs/tags/v1.0`). Packit splits on `/` and
expects exactly three parts, with the second part being `heads` or
`tags`. Pushes with other ref formats are ignored.

#### Deletion handling

If `deleted` is `true`, the event is silently skipped (same behavior
as GitHub).

---

### 2. Pull Request Event

**Topic:** `org.fedoraproject.prod.forgejo.pull_request`

Triggered on pull request actions.

#### Supported actions

Packit only processes these `action` values:

- `opened` — a new PR was created
- `reopened` — a closed PR was re-opened
- `synchronized` — new commits were pushed to the PR

All other actions (e.g. `closed`, `edited`, `label_updated`) are
silently ignored.

**Note:** Forgejo sends `synchronized` while some versions use
`synchronize`. The parser accepts both by normalizing
`synchronized` to `synchronize` internally.

#### Required fields

| Field path                           | Type   | Description                            |
| ------------------------------------ | ------ | -------------------------------------- |
| `action`                             | string | PR action (see supported values above) |
| `pull_request`                       | object | Pull request object                    |
| `pull_request.number`                | int    | PR number / ID                         |
| `pull_request.user.login`            | string | PR author username                     |
| `pull_request.base.repo.owner.login` | string | Target (base) repo owner               |
| `pull_request.base.repo.name`        | string | Target (base) repo name                |
| `pull_request.base.ref`              | string | Target branch name                     |
| `pull_request.head.repo.owner.login` | string | Source (head) repo owner               |
| `pull_request.head.repo.name`        | string | Source (head) repo name                |
| `pull_request.head.ref`              | string | Source branch name                     |
| `pull_request.head.sha`              | string | Latest commit SHA on the PR            |
| `repository.html_url`                | string | Project URL                            |

#### Optional fields

| Field path          | Type   | Description                                   |
| ------------------- | ------ | --------------------------------------------- |
| `pull_request.body` | string | PR description text                           |
| `number`            | int    | Top-level PR number (not used; taken from PR) |
| `commit_id`         | string | Not used by Packit                            |

---

### 3. Issue / PR Comment Event

**Topic:** `org.fedoraproject.prod.forgejo.issue_comment`

Triggered when a comment is created or edited on an issue or pull
request. Forgejo uses the same event type for both; Packit
distinguishes them using the `is_pull` flag.

#### Supported actions

- `created` — a new comment was posted
- `edited` — an existing comment was modified

#### Required fields (common)

| Field path               | Type   | Description                            |
| ------------------------ | ------ | -------------------------------------- |
| `action`                 | string | Comment action (`created` or `edited`) |
| `issue.number`           | int    | Issue or PR number                     |
| `comment.body`           | string | Comment text content                   |
| `comment.id`             | int    | Comment ID                             |
| `comment.user.login`     | string | Username of the commenter              |
| `repository.name`        | string | Repository name                        |
| `repository.html_url`    | string | Project URL                            |
| `repository.owner.login` | string | Repository namespace / owner login     |

#### PR comment: additional required fields

When `is_pull` is `true`, the `pull_request` object must be present
with the following fields:

| Field path                           | Type   | Description                    |
| ------------------------------------ | ------ | ------------------------------ |
| `is_pull`                            | bool   | Must be `true` for PR comments |
| `pull_request`                       | object | Pull request details           |
| `pull_request.head.repo.owner.login` | string | Source repo owner              |
| `pull_request.head.repo.name`        | string | Source repo name               |
| `pull_request.head.repo.html_url`    | string | Source project URL             |
| `pull_request.base.repo.owner.login` | string | Target repo owner              |
| `pull_request.head.ref`              | string | Source branch ref              |
| `pull_request.head.sha`              | string | Latest commit SHA on the PR    |

#### Issue comment: additional optional fields

When `is_pull` is `false` or absent:

| Field path                  | Type   | Description                                       |
| --------------------------- | ------ | ------------------------------------------------- |
| `repository.default_branch` | string | Default branch name; defaults to `main` if absent |

---

### 4. Action Run Events

**Topics:**

- `org.fedoraproject.prod.forgejo.action_run_success`
- `org.fedoraproject.prod.forgejo.action_run_failure`
- `org.fedoraproject.prod.forgejo.action_run_recover`
- `org.fedoraproject.prod.forgejo.action_run_cancelled`

Triggered when a Forgejo Actions workflow run completes (success,
failure, recovery, or cancellation).

#### Supported trigger events

Packit only processes action runs triggered by:

- `pull_request` — a PR-triggered workflow
- `push` — a push-triggered workflow

Runs triggered by other events (e.g. `schedule`, `workflow_dispatch`)
are silently ignored.

#### Required fields

All field paths are relative to `body.run` (the run object inside
the message body).

| Field path               | Type   | Description                                             |
| ------------------------ | ------ | ------------------------------------------------------- |
| `trigger_event`          | string | Event that triggered the run (`pull_request` or `push`) |
| `trigger_user.login`     | string | Username of the person who triggered the run            |
| `title`                  | string | Workflow run title                                      |
| `status`                 | string | Run status (e.g. `success`, `failure`)                  |
| `updated`                | string | Timestamp of last update                                |
| `html_url`               | string | URL of the action run                                   |
| `commit_sha`             | string | Commit SHA associated with the run                      |
| `repository`             | object | Repository object                                       |
| `repository.html_url`    | string | Project URL                                             |
| `repository.name`        | string | Project name                                            |
| `repository.owner.login` | string | Project namespace / owner                               |

#### For `pull_request`-triggered runs

| Field path      | Type   | Description                                          |
| --------------- | ------ | ---------------------------------------------------- |
| `event_payload` | string | JSON-encoded string of the original PR webhook event |

The `event_payload` must be a JSON string that, when parsed, contains:

| Parsed field path       | Type   | Description        |
| ----------------------- | ------ | ------------------ |
| `pull_request.number`   | int    | PR number          |
| `pull_request.url`      | string | PR API URL         |
| `pull_request.head.ref` | string | Source branch name |

#### For `push`-triggered runs

| Field path  | Type   | Description                                             |
| ----------- | ------ | ------------------------------------------------------- |
| `prettyref` | string | Human-readable ref (e.g. branch name or `#<PR-number>`) |

---

## Summary of All Required Topic Names

```
org.fedoraproject.prod.forgejo.push
org.fedoraproject.prod.forgejo.pull_request
org.fedoraproject.prod.forgejo.issue_comment
org.fedoraproject.prod.forgejo.action_run_success
org.fedoraproject.prod.forgejo.action_run_failure
org.fedoraproject.prod.forgejo.action_run_recover
org.fedoraproject.prod.forgejo.action_run_cancelled
```

Packit's parser uses substring matching (`"forgejo.push" in topic`),
so the exact prefix (`org.fedoraproject.prod.` vs
`org.fedoraproject.stg.`) does not matter as long as the topic
contains the substring.

## Notes

- **Message body wrapper:** Messages arrive via Fedora Messaging
  with the Forgejo webhook payload nested under the `body` key and
  the topic under the `topic` key at the top level.
- **Forgejo webhook compatibility:** The expected payload format
  follows the [Forgejo webhook event documentation](https://forgejo.org/docs/latest/user/webhooks/).
  Packit's parsers are designed to handle the standard Forgejo
  webhook payload structure.
- **Field type strictness:** String fields that are expected to be
  non-empty (e.g. `repository.owner.login`) will cause the event to
  be skipped if they are empty strings or `null`.
- **This document reflects the current state of Packit's parsers.**
  Once the Fedora Forge team confirms the actual message format and
  topic names, the parsers will be updated to match any differences.
