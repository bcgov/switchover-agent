import logging
import datetime
from clients.kube import scale_and_wait, delete_pvc, delete_configmap
from clients.tekton import trigger_tekton_build
from transitions.wait_for import WaitFor
from transitions.shared import maintenance_on, scale_health_api, set_in_recovery, update_patroni_spilo_env_vars
from config import config

logger = logging.getLogger(__name__)


def initiate_active_standby(logic_context, py_env: str):
    scale_health_api(config.get('kube_health_namespace'), 
                     config.get('deployment_health_api'), 
                     0, py_env)
    return initiate_standby(logic_context,
                            py_env, 'gold-standby')


def initiate_passive_standby(logic_context, py_env: str):
    set_in_recovery(False, py_env)
    scale_health_api(config.get('kube_health_namespace'), 
                     config.get('deployment_health_api'), 
                     s, py_env)
    return initiate_standby(logic_context,
                            py_env, 'active-passive')

# Transition to active-passive or gold_standby involves
# the following for the Standby site:
# - set is_recovery (done before this is called)
# - ensure health api is scaled appropriately (done before this is called)
# - ensure maintenance mode is on
# - enable patroni as standby
# - trigger deployment (will scale down keycloak, Kong Control Plane)
# - wait for deployment to complete (Tekton Event ID)


def initiate_standby(logic_context, py_env: str, final_state: str):
    logger.info("initiate_standby")

    maintenance_on()

    ns = config.get('solution_namespace')

    patroni = logic_context.patroni

    if patroni['control'] == 'up' and len(patroni['concerns']) == 0 and patroni['leader']['role'] == 'standby_leader':
        logger.warn(
            "Patroni has no concerns and is already a Standby Leader, no further action")
        return None
    else:
        logic_context.clear_triggers()

        update_patroni_spilo_env_vars(
            True, py_env)

        scale_and_wait(ns, 'statefulset',
                       config.get('statefulset_patroni'),
                       "app=%s" % config.get('statefulset_patroni'),
                       0, py_env)

        delete_pvc(ns, 'storage-volume-patroni-spilo-0', py_env)
        delete_configmap(ns, 'patroni-spilo-config', py_env)
        scale_and_wait(ns, 'statefulset',
                       'patroni-spilo', "app=patroni-spilo", 1, py_env)

        logger.debug("Adding Future work to be triggered later...")
        return WaitFor().wait_until(logic_context.patroni_has_no_standby_concerns).then_trigger(
            complete_standby, logic_context, ns, py_env, final_state)


def complete_standby(logic_context, ns: str, py_env: str, final_state: str):
    logger.info("complete_standby starting")

    delete_pvc(ns, 'storage-volume-patroni-spilo-1', py_env)
    delete_pvc(ns, 'storage-volume-patroni-spilo-2', py_env)
    scale_and_wait(ns, 'statefulset',
                   'patroni-spilo', "app=patroni-spilo", 3, py_env)

    logic_context.clear_triggers()

    pipeline_event = trigger_tekton_build(config.get("tekton_trigger_url"),
                                          config.get("tekton_github_repo"),
                                          config.get("tekton_github_ref"),
                                          config.get("tekton_github_hmac_signature"))
    logger.info("Triggered tekton event %s" % pipeline_event['eventID'])

    pipeline = dict(event_id=pipeline_event['eventID'],
                    start_ts=datetime.datetime.now(), maintenance=True)

    logic_context.set_pipeline(pipeline)

    logic_context.update_switchover_state(
        final_state, '', None, py_env)
