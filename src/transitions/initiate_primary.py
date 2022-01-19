import logging
import datetime
from clients.kube import scale
from clients.patroni import set_readonly_cluster, set_primary_cluster
from clients.tekton import trigger_tekton_build
from transitions.shared import maintenance_on, set_in_recovery, update_patroni_spilo_env_vars
from config import config

logger = logging.getLogger(__name__)


def initiate_active_primary(logic_context, namespace: str, patroni_local_url: str, py_env: str):
    set_in_recovery(False, py_env)
    return initiate_primary(logic_context, namespace, patroni_local_url, py_env)


def initiate_passive_primary(logic_context, namespace: str, patroni_local_url: str, py_env: str):
    set_in_recovery(True, py_env)
    return initiate_primary(logic_context, namespace, patroni_local_url, py_env)


# Transition to active-passive or golddr_primary involves
# the following for the Primary site:
# - set is_recovery (done before this is called)
# - ensure maintenance mode is on
# - ensure health api is scaled up
# - enable patroni as master
# - trigger deployment (will scale up keycloak, Kong Control Plane)
# - wait for deployment to complete (Tekton Event ID)
#   - then turn maintenance mode off
def initiate_primary(logic_context, namespace: str, patroni_local_url: str, py_env: str):
    logger.info("initiate_primary")

    maintenance_on(namespace, py_env)

    scale(config.get('kube_health_namespace'), 'deployment',
          config.get('deployment_health_api'), 2, py_env)

    patroni = logic_context.patroni

    if patroni['control'] == 'up' and len(patroni['concerns']) == 0 and patroni['leader']['role'] == 'leader':
        logger.warn(
            "Patroni has no concerns and is already Primary, no further action")
    else:
        set_primary_cluster(patroni_local_url)

        update_patroni_spilo_env_vars(
            namespace, False, py_env)

    pipeline_event = trigger_tekton_build(config.get("tekton_trigger_url"),
                                          config.get("tekton_github_repo"),
                                          config.get("tekton_github_ref"),
                                          config.get("tekton_github_hmac_signature"))
    logger.info("Triggered tekton event %s" % pipeline_event['eventID'])

    pipeline = dict(event_id=pipeline_event['eventID'],
                    start_ts=datetime.datetime.now(), maintenance=False)

    logic_context.set_pipeline(pipeline)
