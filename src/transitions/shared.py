import logging
from config import config
from clients.kube import update_configmap
from clients.kube import restart_deployment, patch_secret
from clients.keycloak import keycloak_service_block, keycloak_service_flow
from clients.maintenance import set_maintenance

logger = logging.getLogger(__name__)


def maintenance_on(namespace: str, py_env: str):
    logger.debug("MAINTENANCE TURNING ON..")

    # Cycle maintenance page (clears out any connections there might be)
    restart_deployment(namespace, config.get(
        'deployment_keycloak_maintenance_page'), py_env)

    # Switch keycloak service to maintenance
    keycloak_service_block()

    # Turn on maintenance alert on Portal
    set_maintenance(config.get('maintenance_url'), True)

    logger.debug("MAINTENANCE ON - OK")


def maintenance_off(namespace: str, py_env: str):
    logger.debug("MAINTENANCE TURNING OFF..")

    # Cycle maintenance page (clears out any connections there might be)
    restart_deployment(namespace, config.get(
        'deployment_keycloak_maintenance_page'), py_env)

    # Switch keycloak service to keycloak
    keycloak_service_flow()

    # Turn off maintenance alert on Portal
    set_maintenance(config.get('maintenance_url'), False)

    logger.debug("MAINTENANCE OFF - OK")


# Setting the in_recovery indicator on the pipeline will force in_maintenance to False
def set_in_recovery(state: bool, py_env: str):
    state_str = 'false'
    if state:
        state_str = 'true'
    spec = {"in_recovery": state_str, "in_maintenance": "false"}
    patch_secret(config.get("tekton_namespace"),
                 config.get("tekton_terraform_tfvars"), py_env, spec)

# Setting the maintenance indicator on the pipeline will force in_recovery to False


def set_in_maintenance(state: bool, py_env: str):
    state_str = 'false'
    if state:
        state_str = 'true'
    spec = {"in_recovery": "false", "in_maintenance": state_str}
    patch_secret(config.get("tekton_namespace"),
                 config.get("tekton_terraform_tfvars"), py_env, spec)


def update_patroni_spilo_env_vars(namespace: str, standby: bool, py_env: str):
    name = config['configmap_patroni_env_vars']
    if standby:
        update = dict(data=dict(
            STANDBY_HOST=config['patroni_peer_host'],
            STANDBY_PORT=config['patroni_peer_port']
        ))
    else:
        update = dict(data=dict(
            STANDBY_HOST="",
            STANDBY_PORT=""
        ))
    update_configmap(namespace, name, py_env, update)
