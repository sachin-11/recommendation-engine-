# RecoEngine on Amazon EKS (learning deployment)

Terraform for a short-lived Kubernetes deployment on EKS: built to learn and demonstrate,
then destroyed. The everyday deployment is the single-host Docker Compose setup in
[docs/docs/deployment.mdx](../../docs/docs/deployment.mdx).

## What it creates (32 resources)

| File | Creates |
|---|---|
| `vpc.tf` | A VPC across 2 availability zones: public subnets (load balancer, NAT gateway), private subnets (worker nodes), one NAT gateway for outbound traffic |
| `eks.tf` | The EKS cluster (Kubernetes 1.35), a managed node group of `t3.medium` nodes in the private subnets, and add-ons: Pod Identity, the EBS CSI driver (disks for Postgres), metrics-server (for `kubectl top` and autoscaling) |
| `ecr.tf` | Private image registries `recoengine/api` and `recoengine/dashboard`, keeping the last 5 images |
| `budget.tf` | An email when the account's monthly spend passes $20, actual or forecast |

Access is through EKS access entries: whoever runs `terraform apply` is cluster admin.

## Cost

Roughly $6–8 a day in `us-east-1`: the EKS control plane ($0.10/hour), two `t3.medium`
nodes, the NAT gateway and a load balancer. Check current AWS prices; destroy when done.

## Steps

```bash
cd infra/eks
terraform init
terraform plan  -var="budget_email=you@example.com"
terraform apply -var="budget_email=you@example.com"      # ~15 minutes

$(terraform output -raw kubeconfig_command)               # point kubectl at the cluster
kubectl get nodes
```

Then build the images and push them to ECR, tagged with the git commit:

```bash
./build-and-push.sh
```

## Tear down

Kubernetes creates some AWS resources itself (the load balancer of an ingress, the EBS
disk of a volume). Terraform does not know them, so delete them through Kubernetes first,
or `terraform destroy` will fail or leave them running and billing:

```bash
kubectl delete namespace recoengine ingress-nginx   # removes the load balancer and disks
terraform destroy -var="budget_email=you@example.com"
```
