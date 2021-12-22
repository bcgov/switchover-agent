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
