# ADR-0010: ECS Fargate and RDS, not Lambda

**Status:** accepted. Validated with `terraform validate`, never applied.

The worker is a long-lived Postgres poller, and agent runs have a 120 s budget that is checked between model calls, so one slow call can overrun it. Lambda would need SQS, a queue rewrite and RDS Proxy. Fargate runs the same image as `docker compose`. The load balancer checks `/healthz`, not `/readyz`, so a stuck worker alarms instead of taking the review API down. Tasks sit in public subnets with inbound only from the load balancer, which avoids a NAT gateway's monthly cost. RDS sits in private subnets. Secret values are set out of band, so they never enter Terraform state.

**Cost:** an idle Fargate task and RDS instance cost money all month, where Lambda would scale to zero.
