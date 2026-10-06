# Log Detective API integration

Packit uses Log Detective (LD) to provide LLM based analysis of failed
downstream Koji builds. When enabled, a failed downstream Koji build creates
one analysis per failed buildArch task.

Reporting requires a Git-backed project URL and a commit SHA.

## Configuration

- `logdetective_enabled`: enables analysis and is set to `False` by default
- `logdetective_token`: used to authenticate requests
- `logdetective_url`: LD server contacted for analysis
- `logdetective_request_timeout`:
  sets timeout on requests to LD API to `30` seconds by default

## Workflow

Packit verifies the three candidate log URLs (`root.log`, `mock_output.log`,
`build.log`), then saves the ordered verified URLs, generated commentary,
and a UUIDv4 on a run linked to the parent build.
These saved inputs are used to build request to LD API `/analyze` endpoint,
with the UUID identifying the analysis request.
Results are retrieved from the `GET /tasks/{id}` LD API endpoint,
using the generated UUID.

A validated `202` response marks the request accepted. The Koji worker submits
immediately after saving the run and schedules a per-run Celery babysitting task
to start after two minutes. The task raises a pending exception while the
analysis is active or a report is unfinished, and Celery retries it up to 14
times with backoff from 30 seconds up to one hour, using the same retry mechanism
as Copr and VM image babysitting. Celery owns the retry countdown. Packit ignores
the API's `Retry-After` header. An uncertain POST is retried with the same
reconstructed body and UUID.
Transient HTTP and envelope failures are retried.

Failed submissions and transient polling errors leave the analysis pending for
Celery's next retry or the hourly recovery scan. A permanent client error while
polling or a terminal LD response records an analysis error. Accepted runs are
polled before applying the seven-day job timeout so a terminal result remains
usable. A run that is still active, or whose result cannot be fetched then,
ends with a timeout error.

The Log Detective worker helper owns submission, polling, and Fedora CI reporting;
the per-run Celery task invokes it with the saved run ID. The run model stores
accepted submissions and provides build and project context for reporting.

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
