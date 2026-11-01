# ADR-0010: ECS Fargate and RDS, not Lambda

**Status:** accepted. Validated with `terraform validate`, never applied.

The worker is a long-lived Postgres poller, and agent runs can take up to 120 s with retries. Lambda would need SQS, a queue rewrite and RDS Proxy. Fargate runs the same image as `docker compose`. Tasks sit in public subnets with inbound only from the load balancer, which avoids a NAT gateway's monthly cost. RDS sits in private subnets. Secret values are set out of band, so they never enter Terraform state.

**Cost:** an idle Fargate task and RDS instance cost money all month, where Lambda would scale to zero.
