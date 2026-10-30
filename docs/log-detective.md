# Log Detective API integration

Packit uses Log Detective (LD) to provide LLM based analysis of failed
downstream Koji and Copr builds. A failed downstream Koji build creates one
analysis per failed buildArch task. A failed Copr build creates one analysis
per failed RPM chroot or SRPM build.

Reporting requires a Git-backed project URL and a commit SHA.

## Configuration

- `logdetective_koji_enabled`: enables Koji analysis; defaults to `False`
- `logdetective_copr_enabled`: enables Copr analysis; defaults to `False`
- `logdetective_token`: used to authenticate requests
- `logdetective_url`: LD server contacted for analysis
- `logdetective_request_timeout`:
  sets timeout on requests to LD API to `30` seconds by default

## Workflow

Packit verifies the three candidate log URLs (`root.log`, `mock_output.log`,
`build.log`), then saves the ordered verified URLs, generated commentary,
and a UUIDv4 on a run linked to the parent build.
For Copr, the build end handler passes the build's log URL to the shared Log
Detective helper, which checks that it returns HTTP 200 with a `text/plain`
content type. The handler then saves the analysis linked to the failed RPM
chroot or SRPM build. The shared run helper submits the saved request and
schedules polling for both Copr and Koji runs.
Both switches respect
`fedora_ci.disabled_projects_for_logdetective`. The old
`logdetective_enabled` option has been replaced by the two switches.
The Copr trigger checks for an explicit failed build status in both RPM and
SRPM failure paths. Failed RPM chroots belonging to one Copr build share an
analysis group, while each chroot keeps its own analysis run and log URL.
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

The Log Detective worker helper owns submission, polling, and Fedora CI reporting;
the per-run Celery task invokes it with the saved run ID. The run model stores
accepted submissions and provides build and project context for reporting.
Report context uses the run's saved build relationship and build system. Copr
RPM and SRPM runs are checked for a newer build of the same commit before
reporting. RPM freshness uses the Copr build group's submission time, and SRPM
freshness uses the SRPM build's submission time. Analysis pipeline clones do
not change which build is considered newer.

For `done`, `error`, `cancelled`, or a timeout, Packit reports to Fedora CI before
storing the terminal result or error. A failed report leaves the run `running`;
the next attempt fetches the LD result again. Reports for deleted or superseded
builds are skipped, then the run is finalized. The database transition is
atomic, so completion metrics are counted once. Concurrent workers may make
duplicate LD requests or Fedora CI reports, and a successful report may be
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
