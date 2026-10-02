# AWS Load Balancer Controller: turns Kubernetes Ingress objects into Application Load
# Balancers. It runs in the cluster (installed with Helm) and needs to create load
# balancers, target groups and security groups in our account, so it gets an IAM role,
# given to its pods through EKS Pod Identity.
#
# The policy is the controller's official one, for the release installed (v3.5.0):
# https://github.com/kubernetes-sigs/aws-load-balancer-controller/blob/v3.5.0/docs/install/iam_policy.json

resource "aws_iam_policy" "load_balancer_controller" {
  name   = "${var.name}-aws-load-balancer-controller"
  policy = file("${path.module}/policies/aws-load-balancer-controller.json")
}

resource "aws_iam_role" "load_balancer_controller" {
  name = "${var.name}-aws-load-balancer-controller"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "pods.eks.amazonaws.com" }
      Action    = ["sts:AssumeRole", "sts:TagSession"]
    }]
  })
}

resource "aws_iam_role_policy_attachment" "load_balancer_controller" {
  role       = aws_iam_role.load_balancer_controller.name
  policy_arn = aws_iam_policy.load_balancer_controller.arn
}

# Pods using this service account (the controller, installed into kube-system) get the role.
resource "aws_eks_pod_identity_association" "load_balancer_controller" {
  cluster_name    = aws_eks_cluster.main.name
  namespace       = "kube-system"
  service_account = "aws-load-balancer-controller"
  role_arn        = aws_iam_role.load_balancer_controller.arn
}
