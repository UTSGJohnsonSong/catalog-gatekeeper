variable "aws_region" {
  type    = string
  default = "ca-central-1"
}

variable "name" {
  type    = string
  default = "catalog-gatekeeper"
}

variable "image_tag" {
  type        = string
  description = "Tag of the image in ECR to deploy."
}

variable "acm_certificate_arn" {
  type        = string
  description = "ACM certificate for the HTTPS listener."
}

variable "shop_domain" {
  type        = string
  description = "The one store this deployment serves, for example my-store.myshopify.com."
}

variable "allowed_cidrs" {
  type        = list(string)
  description = "Who may reach the load balancer. Shopify webhook delivery needs 0.0.0.0/0 unless you restrict /webhooks elsewhere."
  default     = ["0.0.0.0/0"]
}

variable "writeback_enabled" {
  type        = bool
  description = "Env half of the kill switch. Off by default: approved fixes wait until a person turns this on."
  default     = false
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "api_desired_count" {
  type    = number
  default = 1
}

variable "worker_desired_count" {
  type    = number
  default = 1
}

variable "alarm_topic_arn" {
  type        = string
  description = "SNS topic for alarms. Empty means alarms exist but notify nobody."
  default     = ""
}
