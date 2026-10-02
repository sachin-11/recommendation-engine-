# RecoEngine on Amazon EKS (learning deployment)

Terraform for a short-lived Kubernetes deployment on EKS: built to learn and demonstrate,
then destroyed. The everyday deployment is the single-host Docker Compose setup in
[docs/docs/deployment.mdx](../../docs/docs/deployment.mdx).

## What it creates (36 resources)

| File | Creates |
|---|---|
| `vpc.tf` | A VPC across 2 availability zones: public subnets (load balancer, NAT gateway), private subnets (worker nodes), one NAT gateway for outbound traffic |
| `eks.tf` | The EKS cluster (Kubernetes 1.35), a managed node group of `t3.medium` nodes in the private subnets, and add-ons: Pod Identity, the EBS CSI driver (disks for Postgres), metrics-server (for `kubectl top` and autoscaling) |
| `ecr.tf` | Private image registries `recoengine/api` and `recoengine/dashboard`, keeping the last 5 images |
| `budget.tf` | An email when the account's monthly spend passes $20, actual or forecast |
| `load-balancer-controller.tf` | The IAM role (via Pod Identity) the AWS Load Balancer Controller uses to create load balancers, with its official policy in `policies/` |

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

Deploy the app (`k8s/`: Postgres on a gp3 disk, Redis, Mailpit, a migration Job, the API
with probes and an autoscaler, the worker, the dashboard, and an Ingress):

```bash
k8s/make-secret.sh              # API keys from the repo's .env; fresh SECRET_KEY and DB password
helm repo add eks https://aws.github.io/eks-charts
helm install aws-load-balancer-controller eks/aws-load-balancer-controller --version 3.5.0   -n kube-system --set clusterName=recoengine --set region=us-east-1   --set vpcId=$(terraform output -raw vpc_id)   --set serviceAccount.create=true --set serviceAccount.name=aws-load-balancer-controller
kubectl apply -k k8s            # set the image tag in k8s/kustomization.yaml first

# The Ingress gets a public ALB hostname; point emails and CORS at it:
H=$(kubectl -n recoengine get ingress recoengine -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')
kubectl -n recoengine patch configmap recoengine-config --type merge   -p "{\"data\":{\"DASHBOARD_URL\":\"http://$H\",\"ALLOWED_ORIGINS\":\"http://$H\"}}"
kubectl -n recoengine rollout restart deploy/api deploy/worker
echo "http://$H"
```

Emails (e.g. email verification) are caught by Mailpit:
`kubectl -n recoengine port-forward svc/mailpit 8025:8025`, then open http://localhost:8025.

The ingress-nginx controller was retired by the Kubernetes project in 2026, so the Ingress is
served by the AWS Load Balancer Controller as an Application Load Balancer instead.

## Tear down

Kubernetes creates some AWS resources itself (the load balancer of an ingress, the EBS
disk of a volume). Terraform does not know them, so delete them through Kubernetes first,
or `terraform destroy` will fail or leave them running and billing:

```bash
kubectl delete namespace recoengine     # the controller removes the ALB; the disk is deleted
helm uninstall aws-load-balancer-controller -n kube-system
terraform destroy
```
