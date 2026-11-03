resource "aws_iam_role" "execution" {
  name_prefix = "${var.name}-exec-"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Only this deployment's secrets, not every secret in the account.
resource "aws_iam_role_policy" "read_secrets" {
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "secretsmanager:GetSecretValue"
      Resource = [for s in aws_secretsmanager_secret.app : s.arn]
    }]
  })
}

resource "aws_ecs_cluster" "main" {
  name = var.name

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${var.name}"
  retention_in_days = 30
}

locals {
  image = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
  environment = [
    { name = "ENV", value = "production" },
    { name = "LLM_PROVIDER", value = "anthropic" },
    { name = "SHOPIFY_ADAPTER", value = "live" },
    { name = "SHOPIFY_SHOP_DOMAIN", value = var.shop_domain },
    # The client credentials grant gives 24-hour tokens. The adapter asks for a new one before each expires.
    { name = "SHOPIFY_CLIENT_ID", value = var.shopify_client_id },
    { name = "WRITEBACK_ENABLED", value = tostring(var.writeback_enabled) },
    { name = "STATIC_DIR", value = "/app/static" },
    { name = "FIXTURES_DIR", value = "/app/fixtures" },
  ]
  app_secrets = [
    { name = "DATABASE_URL", valueFrom = aws_secretsmanager_secret.app["database-url-app"].arn },
    { name = "SHOPIFY_CLIENT_SECRET", valueFrom = aws_secretsmanager_secret.app["shopify-client-secret"].arn },
    { name = "SHOPIFY_WEBHOOK_SECRET", valueFrom = aws_secretsmanager_secret.app["shopify-webhook-secret"].arn },
    { name = "ANTHROPIC_API_KEY", valueFrom = aws_secretsmanager_secret.app["anthropic-api-key"].arn },
    { name = "REVIEWER_TOKENS", valueFrom = aws_secretsmanager_secret.app["reviewer-tokens"].arn },
  ]
  log_config = {
    logDriver = "awslogs"
    options = {
      awslogs-group         = aws_cloudwatch_log_group.app.name
      awslogs-region        = var.aws_region
      awslogs-stream-prefix = "app"
    }
  }
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${var.name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.execution.arn
  container_definitions = jsonencode([{
    name             = "api"
    image            = local.image
    command          = ["uvicorn", "gatekeeper.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
    portMappings     = [{ containerPort = 8000 }]
    environment      = local.environment
    secrets          = local.app_secrets
    logConfiguration = local.log_config
  }])
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${var.name}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.execution.arn
  container_definitions = jsonencode([{
    name             = "worker"
    image            = local.image
    command          = ["python", "-m", "gatekeeper.worker"]
    environment      = local.environment
    secrets          = local.app_secrets
    logConfiguration = local.log_config
  }])
}

# One-off task, run before each deploy. It connects as the database owner because CREATE EXTENSION needs it.
resource "aws_ecs_task_definition" "migrate" {
  family                   = "${var.name}-migrate"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.execution.arn
  container_definitions = jsonencode([{
    name        = "migrate"
    image       = local.image
    command     = ["python", "-m", "gatekeeper.seed"]
    environment = local.environment
    secrets = [
      { name = "DATABASE_URL", valueFrom = aws_secretsmanager_secret.app["database-url-migrate"].arn },
      { name = "APP_DB_PASSWORD", valueFrom = aws_secretsmanager_secret.app["app-db-password"].arn },
      { name = "MCP_DB_PASSWORD", valueFrom = aws_secretsmanager_secret.app["mcp-db-password"].arn },
      { name = "SHOPIFY_CLIENT_SECRET", valueFrom = aws_secretsmanager_secret.app["shopify-client-secret"].arn },
      { name = "SHOPIFY_WEBHOOK_SECRET", valueFrom = aws_secretsmanager_secret.app["shopify-webhook-secret"].arn },
      { name = "ANTHROPIC_API_KEY", valueFrom = aws_secretsmanager_secret.app["anthropic-api-key"].arn },
      { name = "REVIEWER_TOKENS", valueFrom = aws_secretsmanager_secret.app["reviewer-tokens"].arn },
    ]
    logConfiguration = local.log_config
  }])
}

resource "aws_ecs_service" "api" {
  name            = "api"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.api_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  depends_on = [aws_lb_listener.https]
}

resource "aws_ecs_service" "worker" {
  name            = "worker"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.worker_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = true
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
}

resource "aws_lb" "main" {
  name               = var.name
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id
}

resource "aws_lb_target_group" "api" {
  name        = var.name
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    path                = "/healthz"
    interval            = 15
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 5
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.acm_certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

resource "aws_lb_listener" "http_redirect" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "alb_5xx" {
  alarm_name          = "${var.name}-5xx"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  dimensions          = { LoadBalancer = aws_lb.main.arn_suffix }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 10
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_topic_arn == "" ? [] : [var.alarm_topic_arn]
}

resource "aws_cloudwatch_metric_alarm" "unhealthy_targets" {
  alarm_name          = "${var.name}-unhealthy"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "UnHealthyHostCount"
  dimensions          = { LoadBalancer = aws_lb.main.arn_suffix, TargetGroup = aws_lb_target_group.api.arn_suffix }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  alarm_actions       = var.alarm_topic_arn == "" ? [] : [var.alarm_topic_arn]
}

resource "aws_cloudwatch_metric_alarm" "db_free_storage" {
  alarm_name          = "${var.name}-db-storage"
  namespace           = "AWS/RDS"
  metric_name         = "FreeStorageSpace"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.main.identifier }
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 2000000000
  comparison_operator = "LessThanThreshold"
  alarm_actions       = var.alarm_topic_arn == "" ? [] : [var.alarm_topic_arn]
}

output "load_balancer_dns" {
  value = aws_lb.main.dns_name
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "db_endpoint" {
  value = aws_db_instance.main.address
}
