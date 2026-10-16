# Graph server

The final MHH guideline knowledge graph (`guidelineKG_final_16_10.nq.gz`, one N-Quads file, every statement in the
named graph it comes from) is the only graph source of TIB Chat. This folder runs it as a SPARQL endpoint with
[QLever](https://github.com/ad-freiburg/qlever) (University of Freiburg) on one small Compute Engine VM, as code.

```
graph.nq.gz (bucket, by sha256) ──> VM: qlever index (once per version) ──> qlever start :7001 (SPARQL)
                                                                         └─> qlever ui    :8176 (administrators)
Cloud Run (TIB Chat) ── Direct VPC egress ──> 10.156.x.x:7001      you ── IAP tunnel ──> :8176 / :7001
```

| file | what |
|---|---|
| `terraform/` | bucket for the graph, service account (reads only that bucket), fixed internal address, Cloud NAT for outbound traffic, firewall (IAP and the app subnet only), data disk for the index, the VM |
| `startup.sh` | runs on every boot: mounts the data disk, installs docker and the pinned QLever CLI, downloads the graph version named in the metadata, checks its sha256, builds the index once, starts the server and the UI |
| `Qleverfile` | QLever configuration (N-Quads input, port, UI) |
| `graph.lock.json` | the graph version the server must serve: file name, sha256, bytes, bucket object |
| `publish_graph.py` | uploads a graph version, writes `graph.lock.json`, optionally applies terraform and re-runs the startup script |
| `ui-tunnel.ps1` | opens the QLever UI and the endpoint on your computer through IAP |

## What runs where, and what it costs

- VM `qlever-graph-server`, Debian 12, no public IP, shielded VM, zone europe-west3-a (same region as Cloud Run).
  Serving size `e2-small` (2 vCPU shared, 2 GB). QLever keeps its vocabulary compressed on disk
  (`on-disk-compressed`), so the server needs little memory; `startup.sh` sets the query memory to 35 % and the cache
  to 15 % of the RAM.
- Data disk `qlever-data`, 40 GB pd-balanced, holds the index of every graph version under `/srv/qlever/graphs/<sha256>`.
- Bucket `<project>-graph`: the graph files, one object per version. Terraform state: bucket `<project>-tfstate`.
- Running costs are dominated by the VM, the disk and the NAT gateway; estimate them for the current price list in the
  Google Cloud pricing calculator before changing the machine type. The one-off index build on `e2-standard-4` takes
  well under an hour.

## First setup (done on 16 October 2026)

```
gcloud storage buckets create gs://tib-chat-1011-13273-tfstate --location=EUROPE-WEST3 --uniform-bucket-level-access
cd graph-server/terraform && terraform init -backend-config=backend.hcl
python graph-server/publish_graph.py <final_graph_16_10>/guidelineKG_final_16_10.nq.gz
terraform apply -var machine_type=e2-standard-4   # index build: 16 GB for the build (graph about 10 min, text index about 25 min)
# wait until /var/log/qlever-startup.log on the VM says "ready", then the serving size:
terraform apply                                     # e2-small (the default). the VM restarts, the index stays
```

Once, as project owner (terraform's service account holds no IAM admin role):

```
# the VM may write its logs to Cloud Logging
gcloud projects add-iam-policy-binding tib-chat-1011-13273 --role=roles/logging.logWriter \
  --member=serviceAccount:qlever-graph-server@tib-chat-1011-13273.iam.gserviceaccount.com
# the GitHub deploy account may attach Cloud Run to the subnet (Direct VPC egress)
gcloud compute networks subnets add-iam-policy-binding default --region=europe-west3 --role=roles/compute.networkUser \
  --member=serviceAccount:github-deployer@tib-chat-1011-13273.iam.gserviceaccount.com
```

Then point the app at it: GitHub repository variable `GRAPH_ENDPOINT` = terraform output `sparql_endpoint`
(`deploy/deploy.py` passes it to Cloud Run together with Direct VPC egress into the default subnet).

## A new graph version

```
python graph-server/publish_graph.py --apply <new graph.nq.gz>
```

uploads the file under its sha256 (nothing is overwritten), updates `graph.lock.json`, applies terraform (new
metadata) and re-runs `startup.sh` on the VM. The VM verifies the sha256, builds the new index next to the old one,
and replaces the server. Commit `graph.lock.json` with the app change that needs the new version. For a large graph,
apply `-var machine_type=e2-standard-4` first, as in the first setup.

## The QLever UI

```
powershell -File graph-server/ui-tunnel.ps1     # then http://localhost:8176
```

The UI has a SPARQL editor with autocompletion and result tables. It runs on the VM and is reached only through
Identity-Aware Proxy: your Google account needs `roles/iap.tunnelResourceAccessor` (project owners have it).

Example queries:

```sparql
PREFIX f: <urn:mhh:final:20261016:>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
# the codes of one concept, per terminology, with their status
SELECT ?source ?status ?code ?name WHERE {
  ?ls f:anchor <http://digistrucmed.org/entity/heart_failure> ; f:terminologySource ?source ; f:linkStatus ?status .
  OPTIONAL { ?ls f:effectiveConcept ?c . ?c f:code ?code . OPTIONAL { ?c rdfs:label ?name } }
}
```

```sparql
PREFIX f: <urn:mhh:final:20261016:>
# how many page mentions were selected, rejected or left unconfirmed, per terminology
SELECT ?source ?status (COUNT(?ls) AS ?n) WHERE {
  ?ls a f:EffectiveConceptLinkSet ; f:terminologySource ?source ; f:linkStatus ?status .
} GROUP BY ?source ?status ORDER BY ?source ?status
```

## Operations

- logs of the startup script: `/var/log/qlever-startup.log` on the VM
  (`gcloud compute ssh qlever-graph-server --zone europe-west3-a --tunnel-through-iap`)
- the server container restarts by itself after a crash or a reboot (`unless-stopped`); `startup.sh` is idempotent
- the access token for privileged QLever operations is generated on the VM (`/srv/qlever/access-token`) and never
  leaves it; the app only reads
