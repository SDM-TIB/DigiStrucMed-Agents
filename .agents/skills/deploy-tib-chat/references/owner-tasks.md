# Owner tasks (need Google Cloud credentials of the project owner)

These are not run by GitHub Actions. They need gcloud signed in as an owner of `tib-chat-20261019` (the owner's
service-account key lives outside every repository) and, where noted, the local `.env` (copy of `.env.example`).

## Change a secret

Put the new value in `.env` and run, from the repository root:

```bash
python deploy/deploy.py app
```

It writes changed secrets to Secret Manager over stdin (nothing printed, nothing on disk), grants the app's runtime
account access, and deploys the app with references to the latest versions. Then update nothing in GitHub: secrets
are never repository variables.

## First deploy of a service, or a service that lost its IAM

`--code-only` keeps who may invoke a service. A new service needs the full run once, which also lets the app's
runtime account invoke it and prints its URL for the repository variable:

```bash
python deploy/deploy.py embed          # prints EMBED_URL
python deploy/deploy.py agent          # prints AGENT_URL
python deploy/deploy.py agent-aisdk    # prints AGENT_AISDK_URL
```

## Graph server (QLever on a VM, terraform)

```bash
cd graph-server/terraform && terraform init -backend-config=backend.hcl
python graph-server/publish_graph.py <graph>.nq.gz        # upload under its sha256, update graph.lock.json
terraform apply -var machine_type=e2-standard-4             # index build needs 16 GB
# wait until /var/log/qlever-startup.log on the VM says "ready", then the serving size:
terraform apply
```

The previous graph version keeps serving while a new one indexes. Details: `graph-server/README.md`.
If the VM's internal address changes, update the variable `GRAPH_ENDPOINT` and redeploy the app.

## Lino's GPU (Modal)

```bash
python deploy/deploy.py gpu     # vLLM server in every Modal workspace of the pool
```

## Large assets (videos, LoRA adapter)

```bash
python deploy/deploy.py assets push|pull
```
