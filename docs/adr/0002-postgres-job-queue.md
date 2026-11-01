# ADR-0002: A Postgres job queue, not Redis or SQS

**Status:** accepted.

Jobs live in Postgres and are claimed with `FOR UPDATE SKIP LOCKED`. This lets a status change and its job commit in one transaction: an approval cannot exist without its apply job, and vice versa. There is no second system to run.

**Cost:** throughput is limited by Postgres polling, which is far above this workload but would not suit high volume. A reaper handles workers that die while holding a job.
