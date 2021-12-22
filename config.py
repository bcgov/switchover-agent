import os

config = dict(
    wss_server_host='0.0.0.0',
    wss_server_port=8765,
    active_site='gold',
    passive_site='golddr',
    active_ip='142.34.229.4',
    passive_ip='142.34.64.4',
    deployment_health_api='bcgov-health-api-dev-generic-api',
    deployment_kong_control_plane='konghc-kong',
    deployment_kong_control_plane_label_selector='app.kubernetes.io/instance=konghc',
    deployment_keycloak_maintenance_page='keycloak-maintenance-redirect-generic-api',
    deployment_keycloak_maintenance_page_label_selector='app.kubernetes.io/instance=keycloak-maintenance-redirect',
    statefulset_patroni='patroni-spilo',
    statefulset_keycloak='keycloak',
    statefulset_keycloak_label_selector='app.kubernetes.io/instance=keycloak',
    configmap_patroni='patroni-spilo-config',
    configmap_switchover='switchover-state',
    configmap_patroni_replicas=3,
    configmap_patroni_env_vars='patroni-spilo-env-vars',
    automation_enabled=os.environ.get('AUTOMATION_ENABLED') == 'true',
    patroni_peer_host=os.environ.get("PATRONI_PEER_HOST"),
    patroni_peer_port=os.environ.get("PATRONI_PEER_PORT"),
    kube_health_namespace=os.environ.get("KUBE_HEALTH_NAMESPACE"),
    maintenance_url=os.environ.get("MAINTENANCE_URL")
)
