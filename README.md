# Switchover Agent

## Description

Switchover Agent is used in conjunction with a Global Service Load Balancer (GSLB) to monitor and manage the transition of a Patroni Postgres cluster from Standby to Primary during a Disaster Recovery scenario.

The `Switchover Agent` assumes the Primary and Secondary sites are running Active-Passive, and executes on both the Active and Passive sites, connecting to each other through a Secure Websockets connection.

Each `Switchover Agent` observes:

- The DNS resolution on a domain name that is managed by the F5 Global Traffic Manager
- Switchover Agent Peer connectivity
- Patroni cluster health
- State transitions initiated by updates to a Kubernetes Config Map

When the `Switchover Agent` detects the Active site traffic is down (DNS is resolving to the Passive site or no DNS), it will perform the following actions:

**Passive Site:**

- Set Patroni as Primary cluster
- Scale up the Health Check Service used by the GSLB (if it isn't already)
- Activate the Maintenance messaging on the API Services Portal
- Scale Keycloak (and its dependencies) up and wait for ready
- Deactivate Maintenance messaging

**Active Site:**

The Active site is first put into a state of `golddr-primary`, either by enabling the `AUTOMATION`, or by manually updating the Switchover ConfigMap. Transitioning to this state will:

- Scale down the Health Check Service used by the GSLB

When some stability has returned, the Active site can be manually transition to `gold-standby`, at which time the `Switchover Agent` will perform the following actions:

- Scale down Patroni cluster
- Scale down Keycloak (and its dependencies)
- Update Patroni to Bootstrap in Standby mode
- Delete Patroni 0's PVC and ConfigMaps
- Scale up Patroni 0 (1 Pod)
- Delete Patroni 1 and 2 PVC and ConfigMaps
- Scale up Patroni 1 and 2

And then once the Active site is ready to return to normal operation, the `Switchover Agent` will perform the following actions:

- Set Patroni as Primary cluster
- Scale up the Health Check Service used by the GSLB
- Activate the Maintenance messaging on the API Services Portal
- Scale Keycloak (and its dependencies) up and wait for ready
- Deactivate Maintenance messaging

## Getting Started

### Dependencies

- Docker
- Kubernetes

### Installation

#### Docker

```
docker build --tag switchover.local -f Dockerfile .

export TLS_CA=\_tmp/rootCA.crt
export TLS_LOCAL_CRT=\_tmp/switchover-peer-gold.crt
export TLS_LOCAL_KEY=\_tmp/switchover-peer-gold.key

export PEER_HOST=127.0.0.1
export PEER_PORT=8765

export PROCESS_LIST="logic_handler,peer_server,peer_client,peer_client_fwd,dns_watch,kube_watch"

-- dns_watch
export GSLB_DOMAIN=ggw.dev.api.gov.bc.ca.glb.gov.bc.ca

-- kube_watch
export ENVIRONMENT=local
export KUBE_CLUSTER=gold
export KUBE_NAMESPACE=b8840c-dev
export KUBE_HEALTH_NAMESPACE=b8840c-tools
export KUBE_TEKTON_NAMESPACE=b8840c-tools

-- initialize the switchover-state configmap
echo '
apiVersion: v1
kind: ConfigMap
metadata:
  name: switchover-state-local
  namespace: b8840c-tools
  labels:
    app: switchover
    env: local
    name: switchover-config
data:
  last_stable_state: ""
  last_stable_state_ts: ""
  transition: ""
' | kubectl apply -f -

-- NOTE: the Peer is itself
docker run -ti --rm \
 -p 3303:80 \
 -v `pwd`/\_tmp/pycache:/.cache \
 -v `pwd`/\_tmp:/app/\_tmp \
 -v /Users/aidancope/.kube:/root/.kube \
 -e TLS_CA -e TLS_LOCAL_CRT -e TLS_LOCAL_KEY \
 -e PEER_HOST -e PEER_PORT \
 -e PROCESS_LIST \
 -e GSLB_DOMAIN \
 -e KUBE_CLUSTER -e KUBE_NAMESPACE -e KUBE_HEALTH_NAMESPACE \
 -e KUBE_TEKTON_NAMESPACE -e ENVIRONMENT \
 -e PY_ENV=local \
switchover.local

```

#### Poetry

```bash
brew update
brew install pyenv
pyenv install 3.7
pyenv global 3.7
curl -sSL https://raw.githubusercontent.com/python-poetry/poetry/master/get-poetry.py | python
```

#### Requirements

```bash
export PATH="$HOME/.poetry/bin:$PATH"
poetry env use 3.7
poetry install
```

#### Running

```
poetry run python src/main.py
```

#### Testing

```
openssl genrsa -out switchover-peer.key 2048

export EXT='[ req ]\nprompt = no\ndistinguished_name = dn\nreq_extensions = req_ext\n\n[ dn ]\nCN = switchover\n\n[ req_ext ]\nextendedKeyUsage = serverAuth\nsubjectAltName = @alt_names\n\n[ alt_names ]\nDNS.1 = agent-active.localtest.me\nDNS.3 = agent-passive.localtest.me\n\n'

openssl req -new -sha256 \
 -key switchover-peer.key \
 -extensions req_ext \
 -config <(printf "$EXT") \
 -out switchover-peer.csr

openssl x509 -req -in switchover-peer.csr \
 -CA rootCA.crt -CAkey rootCA.key -CAcreateserial \
 -extensions req_ext \
 -extfile <(printf "$EXT") \
 -out switchover-peer.crt -days 500 -sha256
```

```

docker compose build
docker compose up

curl -v http://localhost:6664/phase/transition-to-golddr-primary -X PUT
curl -v http://localhost:6664/phase/transition-to-gold-standby -X PUT
curl -v http://localhost:6664/phase/transition-to-active-passive -X PUT

curl -v http://localhost:6664/initiate/dns_lookup_error -X PUT
```

#### Test Scenario - Active Network Error

- Exclude the `peer_client_fwd` process from starting to simulate a loss of connectivity between active and passive
- call: `curl -v http://localhost:6664/initiate/dns_lookup_error -X PUT`

At this point, active is in `golddr-primary` and passive is in `active-passive`.

## Configuration

| Environment Variable     | Description                                                           |
| ------------------------ | --------------------------------------------------------------------- |
| TLS_CA                   | The CA Bundle for verifying the Peer certificates                     |
| TLS_LOCAL_CRT            | The Certificate of Self                                               |
| TLS_LOCAL_KEY            | The Key of Self                                                       |
| PEER_HOST                | Peer Host                                                             |
| PEER_PORT                | Peer Port                                                             |
| AUTOMATION_ENABLED       | Perform automatic failover based on feedback from the GSLB            |
| ENVIRONMENT              | `dev`, `test`, `prod` - where the solution is deployed                |
| KUBE_CLUSTER             | The cluster (gold, golddr) that the switchover service is running in  |
| KUBE_NAMESPACE           | Namespace where the solution is deployed (i.e. Patroni, Keycloak)     |
| KUBE_HEALTH_NAMESPACE    | Namespace where the health API and other continuous delivery services |
| KUBE_TEKTON_NAMESPACE    | Namespace that the Tekton pipelines are run in                        |
| TEKTON_URL               | Trigger URL for starting a deployment using Tekton Pipelines          |
| TEKTON_GITHUB_REPO       | The github repository that holds the Terraform configuration          |
| TEKTON_GITHUB_REF        | The github reference passed in the Trigger payload                    |
| TEKTON_HMAC_SECRET       | The HMAC secret used to generate a Signature on the Trigger payload   |
| TERRAFORM_TFVARS         | The Terraform configuration where the in_recovery variable is patched |
| GSLB_DOMAIN              | Domain name that is used to resolve DNS load balancing                |
| PATRONI_PEER_HOST        | Host of the peer patroni cluster                                      |
| PATRONI_PEER_PORT        | Port of the peer patroni cluster                                      |
| PATRONI_LOCAL_API        | URL of the Patroni Control API                                        |
| MAINTENANCE_URL          | Endpoint for the PUT /maintenance/:status and GET /maintenance        |
| PROMETHEUS_MULTIPROC_DIR | Prometheus transient collector db                                     |
| PROCESS_LIST             | Comma-delimited list of processes to start.                           |
| DNS_SERVICE_URL          | Only used for local testing to replace the socket DNS call            |
| LOG_LEVEL                | Comma-delimited list of categories and their log levels               |
|                          | Example: 'clients.dns=INFO,peers.server=INFO,peers.client=INFO'       |

**PROCESS_LIST values:**

| Process         | Description                                                     |
| --------------- | --------------------------------------------------------------- |
| logic_handler   | Evaluates the observations and executes actions based on rules  |
| dns_watch       | Observes the Domain Name Resolution for GSLB managed domain     |
| patroni_worker  | Observes state changes in the Patroni Postgres cluster          |
| kube_watch      | Observes the Switchover Agent ConfigMap                         |
| tekton_watch    | Observes the Tekton Pipeline Runs                               |
| peer_server     | Observes events from the Peer Switchover Agent                  |
| peer_client     | Establishes a Websocket connection to the Peer Switchover Agent |
| peer_client_fwd | Forwards specific events to the Peer Switchover Agent           |

**Default ports:**

- `8000` : Port for the healthcheck `/health` endpoint and Prometheus `/metrics`
- `8765` :

## Deployment

### Generate TLS certificates

**Root CA**

```shell
openssl genrsa -out rootCA.key 4096

openssl req -x509 -new -nodes -key rootCA.key -sha256 -days 1024 \
  -out rootCA.crt -subj "/CN=aps_root_ca"

kubectl  create secret generic kongh-cluster-ca \
  --from-file=ca.crt=./rootCA.crt

```

**Switchover TLS**

```
openssl genrsa -out switchover-peer.key 2048

export EXT='[ req ]\nprompt = no\ndistinguished_name = dn\nreq_extensions = req_ext\n\n[ dn ]\nCN = switchover\n\n[ req_ext ]\nextendedKeyUsage = serverAuth\nsubjectAltName = @alt_names\n\n[ alt_names ]\nDNS.1 = bcgov-switchover-transport-switchover\nDNS.2 = bcgov-switchover-transport-switchover-gold\nDNS.3 = bcgov-switchover-transport-switchover-golddr\n\n'

openssl req -new -sha256 \
 -key switchover-peer.key \
 -extensions req_ext \
 -config <(printf "$EXT") \
 -out switchover-peer.csr

openssl x509 -req -in switchover-peer.csr \
 -CA rootCA.crt -CAkey rootCA.key -CAcreateserial \
 -extensions req_ext \
 -extfile <(printf "$EXT") \
 -out switchover-peer.crt -days 500 -sha256

openssl x509 -in switchover-peer.crt -text -noout

kubectl create secret \
  tls switchover-cert --cert=./switchover-peer.crt --key=./switchover-peer.key

```

### Helm Deployment using Terraform

**Transport Claim between Gold and GoldDR**

```
resource "helm_release" "bcgov-switchover-transport" {
  name       = "bcgov-switchover-transport"
  repository = "http://bcgov.github.io/helm-charts"
  chart      = "ocp-transport-claim"
  version    = "0.1.2"

  namespace = var.namespace

  wait = true

  values = [
    <<EOT
transports:
  enabled: ${local.dc == "gold" ? true : false}

claims:
- name: switchover
  servicePort: 8765
  targetPort: 8765
  envSuffix: "${upper(local.dc_peer)}_SERVICE_PORT"
  selectorLabels:
    app.kubernetes.io/instance: bcgov-switchover
    app.kubernetes.io/name: generic-api

EOT
  ]
}

data "kubernetes_service" "bcgov-switchover-peer" {
  depends_on = [helm_release.bcgov-switchover-transport]
  metadata {
    name      = "bcgov-switchover-transport-switchover-${local.dc_peer}"
    namespace = var.namespace
  }
}
```

**Switchover Agent**

```
resource "helm_release" "bcgov-switchover" {
  name       = "bcgov-switchover"
  repository = "http://bcgov.github.io/helm-charts"
  chart      = "generic-api"
  version    = "0.1.21"

  namespace = var.namespace

  wait = false

  values = [
    <<EOT
image:
  pullPolicy: ${local.pullPolicy}
  repository: ghcr.io/bcgov-dss/api-serv-infra/switchover
  tag: ${local.versions.bcgov-switchover}

imagePullSecrets:
  - name: ${var.workspace}-github-read-packages-creds

podSecurityContext:
  fsGroup: ${var.securityContext["fsGroup"]}

podAnnotations:
  'prometheus.io/scrape': 'true'
  'prometheus.io/port': '8000'
  'prometheus.io/path': '/metrics'

securityContext:
  runAsUser: ${var.securityContext["runAsUser"]}

replicaCount: 1

rollingUpdate:
  maxUnavailable: 100%
  maxSurge: 0%

containerPort: 8000

ingress:
  enabled: false

resources:
  requests:
    cpu: 20m
    memory: 200Mi
  limits:
    cpu: 100m
    memory: 400Mi

rbac:
  create: true
  rules:
    - apiGroups: [""]
      resources: ["configmaps", "services"]
      verbs: ["list", "get", "watch", "update", "patch", "delete"]
    - apiGroups: [""]
      resources: ["pods"]
      verbs: ["list", "get", "watch"]
    - apiGroups: ["apps"]
      resources: ["statefulsets", "statefulsets/scale", "deployments", "deployments/scale"]
      verbs: ["list", "get", "watch", "update", "patch"]
    - apiGroups: [""]
      resources: ["persistentvolumeclaims"]
      verbs: ["list", "get", "delete"]

livenessProbe:
  httpGet:
    path: /health
    port: http
readinessProbe:
  httpGet:
    path: /health
    port: http

extraLabels:
  app: switchover-api

extraPorts:
- name: wss
  port: 8765

extraVolumes:
  - name: py-cache-vol
    emptyDir: {}

extraVolumeMounts:
  - mountPath: /.cache
    name: py-cache-vol

secretVolumes:
- switchover-cert
- kongh-cluster-ca

env:
  TLS_CA:
    value: "/etc/secrets/kongh-cluster-ca/ca.crt"
  TLS_LOCAL_CRT:
    value: "/etc/secrets/switchover-cert/tls.crt"
  TLS_LOCAL_KEY:
    value: "/etc/secrets/switchover-cert/tls.key"
  PEER_HOST:
    value: "${data.kubernetes_service.bcgov-switchover-peer.metadata.0.name}"
  PEER_PORT:
    value: "${data.kubernetes_service.bcgov-switchover-peer.spec.0.port.0.port}"
  KUBE_CLUSTER:
    value: ${local.dc}
  KUBE_NAMESPACE:
    value: ${var.namespace}
  KUBE_HEALTH_NAMESPACE:
    value: ${var.tools_namespace}
  KUBE_TEKTON_NAMESPACE:
    value: ${var.tools_namespace}
  CONFIGMAP_SELECTOR:
    value: app=switchover,name=switchover-config
  AUTOMATION_ENABLED:
    value: 'false'
  GSLB_DOMAIN:
    value: ${var.workspace == "dev" ? "ggw.dev.api.gov.bc.ca.glb.gov.bc.ca" : "ggw.api.gov.bc.ca.glb.gov.bc.ca"}
  PROMETHEUS_MULTIPROC_DIR:
    value: /tmp
  PY_ENV:
    value: production
  PATRONI_PEER_HOST:
    value: "patroni-spilo-transport-patroni-${local.dc_peer}"
  PATRONI_PEER_PORT:
    value: '${data.kubernetes_service.patroni-spilo-peer.spec.0.port.0.port}'
  PATRONI_LOCAL_HOST:
    value: patroni-spilo
  PATRONI_LOCAL_PORT:
    value: '5432'
  PATRONI_LOCAL_API:
    value: 'http://patroni-spilo-control'
  MAINTENANCE_URL:
    value: 'http://bcgov-aps-portal-generic-api'
  LOG_LEVEL:
    value: 'clients.dns=INFO,peers.server=INFO,peers.client=INFO'
  PROCESS_LIST:
    value: 'logic_handler,dns_watch,kube_watch,tekton_watch,peer_server,peer_client,peer_client_fwd,patroni_worker'
EOT
  ]
}
```
