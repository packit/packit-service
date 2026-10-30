# Log Detective API integration

Packit uses Log Detective (LD) to provide LLM based analysis of failed
downstream Koji and Copr builds. A failed downstream Koji build creates one
analysis per failed buildArch task. A failed Copr build creates one analysis
per failed RPM chroot or SRPM build.

Reporting requires a Git-backed project URL and a commit SHA.
The commit SHA selects the Git forge revision for a check. Copr check names
include the target chroot (or `srpm-builds`) and Copr build ID, so analyses
from different submitted builds on the same commit have distinct checks. A
single Copr build ID can cover multiple chroots, which remain distinct by
their target names. The build ID comes from the saved run, allowing recovery
to report the same check after a worker restart.

## Configuration

| Setting                                        | Location                          | Default                                       | Effect                                                                                                                               |
| ---------------------------------------------- | --------------------------------- | --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `log_detective_copr_enabled`                   | Service config                    | `false`                                       | Global gate for new Copr analyses. When `false`, an enabled job cannot start an analysis.                                            |
| `log_detective_enabled`                        | Packit package or Copr job config | `true`                                        | Per-package and per-job Copr opt-out. A job value overrides its package value; `jobs[].packages` can override an individual package. |
| `log_detective_koji_enabled`                   | Service config                    | `false`                                       | Global gate for new downstream Koji analyses.                                                                                        |
| `fedora_ci.disabled_projects_for_logdetective` | Service config                    | Empty list                                    | Project URLs excluded from Koji analysis; does not affect Copr.                                                                      |
| `logdetective_url`                             | Service config                    | `https://logdetective01.fedorainfracloud.org` | Log Detective API server used by both build systems.                                                                                 |
| `logdetective_token`                           | Service config                    | Empty string                                  | Bearer token sent to the Log Detective API.                                                                                          |
| `logdetective_request_timeout`                 | Service config                    | `30` seconds                                  | Timeout for each Log Detective API request.                                                                                          |

A failed Copr build starts an analysis only when both Copr switches are enabled.
Koji analysis requires its service switch and a project absent from the disabled
list. These gates control creation of new analyses; saved runs continue through
submission, polling, and reporting. The old service key `logdetective_enabled`
is no longer accepted by the service config schema.

## Workflow

Packit verifies the three candidate log URLs (`root.log`, `mock_output.log`,
`build.log`), then saves the ordered verified URLs, generated commentary,
and a UUIDv4 on a run linked to the parent build.
For Copr, the build end handler delegates to a Copr trigger helper. It checks
`builder-live.log` and `backend.log` for both RPM chroot and SRPM builds.
Available logs are saved when their URLs return HTTP 200 with a `text/plain`
content type; at least one must verify to start an analysis. The
analysis is linked to the failed RPM chroot or SRPM build. Copr and
Koji trigger helpers share commentary generation: both record available PR,
branch, or release context and build duration, with build-system-specific
metadata and log guidance. Koji also includes NVR, architecture, scratch and
sidetag context, and submission output when available. Copr includes its
project, package, build ID, and RPM or SRPM target. Missing context is omitted.
For Copr RPM analyses, the saved `identifier` comes from the RPM build target's
job configuration identifier. SRPM analyses leave it unset because SRPM builds
do not store a job configuration identifier. PR IDs, tags, and branch names are
source context, not job identifiers.
The shared run helper submits the saved request and schedules polling for both
Copr and Koji runs.
The Copr trigger checks for an explicit failed build status in both RPM and
SRPM failure paths. Failed RPM chroots belonging to one Copr build share an
analysis group, while each chroot keeps its own analysis run and log URL.
An older RPM or SRPM build is skipped when a newer build of the same source
and target exists. If the SRPM has already failed, its analysis is retained
and later failed chroot events do not start additional analyses.
Concurrent deliveries for the same Copr build serialize on the build row and
reuse its existing API run. The run and its analysis group are committed in
one transaction before the first submission; a duplicate delivery does not
submit or schedule another polling task. A missing Copr build group, including
a null group ID, is logged and skips LD analysis while Copr event handling
continues. The service does not create a replacement group in that case.
These saved inputs are used to build request to LD API `/analyze` endpoint,
with the UUID identifying the analysis request.
Results are retrieved from the `GET /tasks/{id}` LD API endpoint,
using the generated UUID.

A validated `202` response marks the request accepted. The build worker submits
immediately after saving the run and schedules a per-run Celery babysitting task
to start after two minutes. The task raises a pending exception while the
analysis is active or a report is unfinished, and Celery retries it up to 14
times with backoff from 30 seconds up to one hour, using the same retry mechanism
as Copr and VM image babysitting. Celery owns the retry countdown. Packit ignores
the API's `Retry-After` header. An uncertain POST is retried with the same
reconstructed body and UUID.
Each trigger logs transient HTTP and envelope failures and leaves the saved run
pending; scheduling proceeds so the request can be retried.

Failed submissions and transient polling errors leave the analysis pending for
Celery's next retry or the hourly recovery scan. A permanent client error while
polling or a terminal LD response records an analysis error. Accepted runs are
polled before applying the seven-day job timeout so a terminal result remains
usable. A run that is still active, or whose result cannot be fetched then,
ends with a timeout error.

The Log Detective worker helper owns submission, polling, and Git forge check reporting;
the per-run Celery task invokes it with the saved run ID. The run model stores
accepted submissions and provides build and project context for reporting.
Report context uses the run's saved build relationship and build system. Copr
RPM and SRPM runs are checked for a newer build of the same commit before
submission, retry, and reporting. A saved run superseded before submission or
retry is finished without an LD request or Git forge check. RPM freshness uses
the Copr build group's submission time, and SRPM
freshness uses the SRPM build's submission time. Analysis pipeline clones do
not change which build is considered newer.

For `done`, `error`, `cancelled`, or a timeout, Packit reports a Git forge check before
storing the terminal result or error. A failed report leaves the run `running`;
the next attempt fetches the LD result again. Reports for deleted or superseded
builds are skipped, then the run is finalized. The database transition is
atomic, so completion metrics are counted once. Concurrent workers may make
duplicate LD requests or Git forge checks, and a successful report may be
repeated if the worker fails before committing the terminal status. If LD no
longer retains the result when a failed report is retried, the result may be
unavailable.

Celery Beat runs an hourly recovery scan in the long-running queue. It reads all
running API analyses and advances them inline, as the Copr and VM image recovery scans do.
Each run is handled independently so a failure does not stop the rest of the
scan. The per-run checker logs unexpected failures and leaves the run pending for
the next Celery retry or Beat scan. Beat does not enqueue another per-run task.
A run still pending after its Celery retries are exhausted remains eligible for
later Beat scans until it finishes or reaches the seven-day timeout.
