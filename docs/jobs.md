# Background jobs: cron or Celery

Four jobs keep everything current. Each is safe to run any number of times.

| Job | Default interval | What it does |
|---|---|---|
| `finalize` | 5 min | Closes recordings quiet for `SESSION_IDLE_MINUTES`, then runs every signal rule on each closed recording not yet analysed and stores its counters and markers. |
| `navigation` | 15 min | Stores quick backs and loops for visits with a page view in the last 2 hours. |
| `heatmaps` | 60 min | Rebuilds today's and yesterday's heatmap buckets (UTC) for every site with recent hits. |
| `prune` | daily | Deletes whatever is past its retention window. |

Change the intervals with `OSS_CLARITY["JOB_INTERVALS"]`.

## With cron

One line, every five minutes, runs whatever is due:

```cron
*/5 * * * *  cd /srv/app && python manage.py oss_clarity_run_jobs
```

Useful options:

```sh
python manage.py oss_clarity_run_jobs --list           # each job and its last run
python manage.py oss_clarity_run_jobs --only heatmaps  # one job, if due
python manage.py oss_clarity_run_jobs --only heatmaps --force  # now, ignoring the interval
python manage.py oss_clarity_prune                     # prune now
```

The command exits non-zero when a job fails, so cron can mail you.

## With Celery

```sh
pip install "oss-clarity[django,celery]"
```

The tasks are registered as `oss_clarity.finalize`, `oss_clarity.navigation`,
`oss_clarity.heatmaps` and `oss_clarity.prune`. Add the suggested schedule to
beat:

```python
from oss_clarity.tasks import BEAT_SCHEDULE

app.conf.beat_schedule = {**app.conf.beat_schedule, **BEAT_SCHEDULE}
```

Each entry expires before its next run, so a backed-up queue does not stack
copies of the same job.

## Why a job never runs twice

Every run, from cron or Celery, first claims the job's row in `JobRun` with one
conditional UPDATE. Only the process whose update changed the row runs the job.
So overlapping cron runs, several servers, or cron and Celery together cannot
run a job twice at once. A run that started but never finished (a crashed
worker) can be taken over after four intervals. Failures are recorded on the row
and retried at the next interval. The admin shows `JobRun` read-only.
