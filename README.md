# Switchover Agent

## Description

## Getting Started

### Dependencies

- Docker
- Kubernetes

### Installation

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

## Configuration

| Environment Variable     | Description                                                           |
| ------------------------ | --------------------------------------------------------------------- |
| TLS_CA                   | The CA Bundle for verifying the Peer certificates                     |
| TLS_LOCAL_CRT            | The Certificate of Self                                               |
| TLS_LOCAL_KEY            | The Key of Self                                                       |
| PEER_HOST                | Peer Host                                                             |
| PEER_PORT                | Peer Port                                                             |
| AUTOMATION_ENABLED       | Perform automatic failover based on feedback from the GSLB            |
| KUBE_CLUSTER             | The cluster (gold, golddr) that the switchover service is running in  |
| KUBE_NAMESPACE           | Namespace where the configmap is located                              |
| KUBE_HEALTH_NAMESPACE    | Namespace where the health API and other continuous delivery services |
| CONFIGMAP_SELECTOR       | Label selector for the configmap that controls state                  |
| GSLB_DOMAIN              | Domain name that is used to resolve DNS load balancing                |
| PATRONI_PEER_HOST        | Host of the peer patroni cluster                                      |
| PATRONI_PEER_PORT        | Port of the peer patroni cluster                                      |
| PATRONI_LOCAL_API        | URL of the Patroni Control API                                        |
| MAINTENANCE_URL          | Endpoint for the PUT /maintenance/:status and GET /maintenance        |
| PROMETHEUS_MULTIPROC_DIR | Prometheus transient collector db                                     |

Default ports:

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

EOT
  ]
}
```
