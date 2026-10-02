# A free public TLS certificate for the app's hostname, used by the ALB for HTTPS.
# The domain's DNS is not in Route 53 (it stays with its registrar, e.g. GoDaddy), so the
# validation record printed by `terraform output certificate_validation` is added there by
# hand; ACM issues the certificate a few minutes after it sees the record.

resource "aws_acm_certificate" "app" {
  count             = var.app_domain == "" ? 0 : 1
  domain_name       = var.app_domain
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}
