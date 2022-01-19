import logging
import datetime
import traceback
import sys
import time
from clients.tekton import trigger_tekton_build
from clients.kube import scale, scale_and_wait, delete_pvc, delete_configmap
from clients.kube import get_configmap, update_configmap, restart_deployment, patch_secret
from clients.patroni import set_readonly_cluster, set_primary_cluster
from clients.maintenance import set_maintenance
from clients.keycloak import keycloak_service_block, keycloak_service_flow
from config import config
from prometheus_client import Gauge, Counter, Enum

logger = logging.getLogger(__name__)


class WaitFor:
    condition = None
    action = None
    args = None

    def wait_until(self, condition):
        self.condition = condition
        return self

    def then_trigger(self, action, *args):
        self.action = action
        self.args = args
        return self

    def eval(self):
        logger.debug("WaitFor Eval Condition : %s" % self.condition())
        if self.condition():
            self.action(*self.args)
            return True
        else:
            return False


class Logic:
    peer = "unknown"
    patroni = dict(control='unknown', concerns=[], leader=dict(role='unknown'))
    pipeline = dict(event_id=None, start_ts=None,
                    end_ts=None, maintenance=False)

    triggers = []
    PIPELINE = Counter('switchover_pipeline', 'Switchover Tekton Pipelines',
                       ['release', 'state'])
    METRIC = Counter('switchover_logic', 'Switchover Logic',
                     ['resource', 'state'])
    GAUGE = Gauge('switchover_logic_gauge', 'Switchover Logic Point in Time',
                  ['resource'])

    def handler(self, cluster: str, namespace: str, label_selector: str, patroni_local_url: str, py_env: str, _q, fwd_to_peer_q):
        while True:
            try:
                item = _q.get()
                logger.info(f'logic {item["event"]}')
                self.METRIC.labels(resource="logic", state="info").inc()

                if item['event'] == 'kube_stream':
                    spec = item['data']['object']
                    kind = spec['kind']

                    if kind == 'PipelineRun':
                        mdnm = spec['metadata']['name']
                        event_id = spec['metadata']['labels']['triggers.tekton.dev/triggers-eventid']
                        logger.debug("   name  = %s" % mdnm)
                        logger.debug(
                            "   event = %s" % event_id)
                        params = self.pick_params(spec['spec']['params'], [
                            "git-release-branch", "release-namespace"])
                        for key in params.keys():
                            logger.debug("   param  %-20s = %s" %
                                         (key, params[key]))
                        status_reason = "Undefined"
                        if 'status' in spec and 'conditions' in spec['status']:
                            status_reason = spec['status']['conditions'][0]['reason']
                            logger.debug("   reason = %s" %
                                         spec['status']['conditions'][0]['reason'])
                            logger.debug("   status = %s" %
                                         spec['status']['conditions'][0]['status'])
                            logger.debug("   messag = %s" %
                                         spec['status']['conditions'][0]['message'])

                            if self.pipeline['event_id'] == event_id and (status_reason == "Succeeded" or status_reason == "Failed"):
                                logger.info("End State for Pipeline!")
                                logger.info("Tekton Start: %s" %
                                            self.pipeline['start_ts'])
                                logger.info("Tekton   End: %s" %
                                            datetime.datetime.now())
                                # turn off maintenance
                                if self.pipeline['maintenance']:
                                    self.maintenance_on(namespace, py_env)
                                else:
                                    self.maintenance_off(namespace, py_env)
                                self.pipeline = dict(
                                    event_id=None, start_ts=None, end_ts=None)

                        if item['data']['type'] != "ADDED":
                            self.PIPELINE.labels(
                                release=params['release-namespace'], state=status_reason).inc()

                        # Status Reason/Status : Running, Unknown
                        # Status Reason/Status : Succeeded, True
                        # Status Reason/Status : Failed, False

                if item['event'] == 'patroni':
                    self.patroni = item
                    if item['control'] == 'up':
                        self.METRIC.labels(
                            resource="patroni", state=("IsStandby=%s" % item['is_standby_configured'])).inc()
                        self.METRIC.labels(
                            resource="patroni-leader", state="%s-%s" % (item['leader']['member'], item['leader']['role'])).inc()
                        for concern in item['concerns']:
                            self.METRIC.labels(
                                resource="patroni-members", state="%s-%s" % (concern['member'], concern['state'])).inc()

                    if item['control'] == 'up' and item['leader']['role'] == 'leader':
                        self.GAUGE.labels(resource="patroni").set(1)
                    elif item['control'] == 'up' and item['leader']['role'] == 'standby_leader':
                        self.GAUGE.labels(resource="patroni").set(2)
                    else:
                        self.GAUGE.labels(resource="patroni").set(0)

                    for trigger in self.triggers:
                        trigger.eval()

                if item['event'] == 'peer':
                    self.peer = item['state']
                    self.METRIC.labels(resource="peer", state=self.peer).inc()

                    if self.peer == 'ok':
                        self.GAUGE.labels(resource="peer").set(1)
                    else:
                        self.GAUGE.labels(resource="peer").set(0)

                if item['event'] == 'dns':
                    dns = item['result']
                    if dns == config.get('active_ip'):
                        self.METRIC.labels(
                            resource="dns", state="active/%s" % dns).inc()
                        self.GAUGE.labels(resource="dns").set(1)

                    elif dns == config.get('passive_ip'):
                        self.METRIC.labels(
                            resource="dns", state="passive/%s" % dns).inc()
                        self.GAUGE.labels(resource="dns").set(2)
                    else:
                        self.METRIC.labels(
                            resource="dns", state="%s" % dns).inc()
                        self.GAUGE.labels(resource="dns").set(0)

                    # Trigger golddr-primary IF:
                    #   Automation is enabled
                    #   This is the Active site but DNS is not going to the Active site
                    #   This is the Passive site and DNS is going to the Passive site
                    if config.get('automation_enabled'):
                        self.GAUGE.labels(resource="automation").set(1)

                        check_active_site = (cluster == config.get('active_site')
                                             and dns != config.get('active_ip'))

                        check_passive_site = (cluster == config.get(
                            'passive_site') and dns == config.get('passive_ip'))

                        if check_active_site or check_passive_site:
                            logger.warn("Transitioning to golddr-primary")
                            self.update_switchover_state(
                                namespace, None, 'golddr-primary', py_env)
                    else:
                        self.GAUGE.labels(resource="automation").set(0)

                    fwd_to_peer_q.put({"event": "from_peer", "message": item})

                if item['event'] == 'from_peer':
                    # messages from the peer will be for the following:
                    # - item.message.event == dns
                    # - item.message.event == transition_to (state = active-passive and gold-standby)
                    if item['message']['event'] == 'transition_to':
                        self.update_switchover_state(
                            namespace, None, item['message']['state'], py_env)

                if item['event'] == 'switchover_state':
                    transition = item['data']['transition']
                    self.METRIC.labels(
                        resource="switchover_state", state=transition).inc()

                    if item['data']['last_stable_state'] == "":
                        self.update_switchover_state(
                            namespace, 'independent', None, py_env)
                        continue

                    if transition == '':
                        logger.debug(
                            "Configmap update - no transition requested")
                        self.GAUGE.labels(resource="transition").set(0)

                    elif transition != item['data']['last_stable_state']:
                        self.GAUGE.labels(resource="transition").set(1)

                        last_stable_state = item['data']['last_stable_state']
                        # initiate a transition
                        next_state = last_stable_state
                        if transition == 'active-passive':
                            if self.peer == 'error':
                                logger.warn(
                                    "Aborting active-passive transition - peer not reachable")
                                self.METRIC.labels(
                                    resource="logic", state="warning").inc()

                            elif last_stable_state != 'independent' and last_stable_state != 'golddr-maintenance' and last_stable_state != 'gold-standby':
                                logger.warn(
                                    "Aborting active-passive transition - can not transition from %s" % last_stable_state)
                                self.METRIC.labels(
                                    resource="logic", state="warning").inc()

                            elif cluster == config.get('active_site'):
                                self.set_in_recovery(False, py_env)

                                # TODO: Do a: is_peer_happy_to_proceed()
                                self.initiate_primary(
                                    namespace, patroni_local_url, py_env)
                                # Let the Passive peer know active-passive should happen
                                fwd_to_peer_q.put({"event": "from_peer", "message": {
                                                  "event": "transition_to", "state": transition}})

                                next_state = transition
                            elif cluster == config.get('passive_site'):
                                self.set_in_recovery(False, py_env)

                                work = self.initiate_passive_standby(
                                    namespace, patroni_local_url, py_env)
                                if work is None:
                                    next_state = transition
                                else:
                                    self.triggers.append(work)
                                    next_state = "%s-partial" % transition

                            self.update_switchover_state(
                                namespace, next_state, '', py_env)

                        elif transition == 'gold-standby':

                            if self.peer == 'error':
                                logger.warn(
                                    "Aborting gold-standby transition - peer not reachable")
                                self.METRIC.labels(
                                    resource="logic", state="warning").inc()

                            elif last_stable_state != 'golddr-primary':
                                logger.warn(
                                    "Aborting gold-standby transition - can only transition from golddr-primary")
                                self.METRIC.labels(
                                    resource="logic", state="warning").inc()

                            elif cluster == config.get('active_site'):
                                # TODO: Do a: is_peer_happy_to_proceed()
                                work = self.initiate_active_standby(
                                    namespace, patroni_local_url, py_env)
                                if work is None:
                                    next_state = transition
                                else:
                                    self.triggers.append(work)
                                    next_state = "%s-partial" % transition

                                # Let the Passive peer know gold-standby should happen
                                fwd_to_peer_q.put({"event": "from_peer", "message": {
                                                  "event": "transition_to", "state": transition}})

                            elif cluster == config.get('passive_site'):
                                self.initiate_primary(
                                    namespace, patroni_local_url, py_env)
                                next_state = transition

                            self.update_switchover_state(
                                namespace, next_state, '', py_env)

                        elif transition == 'golddr-primary':

                            if cluster == config.get('active_site'):
                                self.set_in_recovery(True, py_env)

                                self.initiate_down(
                                    namespace, patroni_local_url, py_env)

                                # Let the Passive peer know golddr-primary should happen
                                # : if AUTOMATION_ENABLED, then GSLB should trigger this anyway on peer
                                fwd_to_peer_q.put({"event": "from_peer", "message": {
                                                  "event": "transition_to", "state": transition}})

                                next_state = transition

                            elif cluster == config.get('passive_site'):
                                self.set_in_recovery(True, py_env)

                                self.initiate_primary(
                                    namespace, patroni_local_url, py_env)
                                next_state = transition

                            self.update_switchover_state(
                                namespace, next_state, '', py_env)

                        else:
                            logger.error(
                                "Unsupported transition '%s'" % transition)
                    else:
                        logger.debug(
                            "Configmap update - last state is already same as transition - no work to do")
                        self.update_switchover_state(
                            namespace, None, '', py_env)

            except Exception as ex:
                logger.error(
                    'Unknown error in logic. %s' % ex)
                traceback.print_exc(file=sys.stdout)
                self.METRIC.labels(resource="logic", state="error").inc()

    def initiate_down(self, namespace: str, patroni_local_url: str, py_env: str):
        logger.info("initiate_down - health down and database paused")

        scale(config.get('kube_health_namespace'), 'deployment',
              config.get('deployment_health_api'), 0, py_env)

    # Transition to active-passive or golddr_primary involves
    # the following for the Primary site:
    # - set is_recovery (done before this is called)
    # - ensure maintenance mode is on
    # - ensure health api is scaled up
    # - enable patroni as master
    # - trigger deployment (will scale up keycloak, Kong Control Plane)
    # - wait for deployment to complete (Tekton Event ID)
    #   - then turn maintenance mode off
    def initiate_primary(self, namespace: str, patroni_local_url: str, py_env: str):
        logger.info("initiate_primary")

        self.maintenance_on(namespace, py_env)

        scale(config.get('kube_health_namespace'), 'deployment',
              config.get('deployment_health_api'), 2, py_env)

        if self.patroni['control'] == 'up' and len(self.patroni['concerns']) == 0 and self.patroni['leader']['role'] == 'leader':
            logger.warn(
                "Patroni has no concerns and is already Primary, no further action")
        else:
            set_primary_cluster(patroni_local_url)

            self.update_patroni_spilo_env_vars(
                namespace, False, py_env)

        pipeline_event = trigger_tekton_build(config.get("tekton_trigger_url"),
                                              config.get("tekton_github_repo"),
                                              config.get("tekton_github_ref"),
                                              config.get("tekton_github_hmac_signature"))
        logger.info("Triggered tekton event %s" % pipeline_event['eventID'])
        self.pipeline['event_id'] = pipeline_event['eventID']
        self.pipeline['start_ts'] = datetime.datetime.now()
        self.pipeline['maintenance'] = False

    def initiate_active_standby(self, namespace: str, patroni_local_url: str, py_env: str):
        scale(config.get('kube_health_namespace'), 'deployment',
              config.get('deployment_health_api'), 0, py_env)
        return self.initiate_standby(namespace, patroni_local_url,
                                     py_env, 'gold-standby')

    def initiate_passive_standby(self, namespace: str, patroni_local_url: str, py_env: str):
        scale(config.get('kube_health_namespace'), 'deployment',
              config.get('deployment_health_api'), 2, py_env)
        return self.initiate_standby(namespace, patroni_local_url,
                                     py_env, 'active-passive')

    # Transition to active-passive or gold_standby involves
    # the following for the Standby site:
    # - set is_recovery (done before this is called)
    # - ensure health api is scaled appropriately (done before this is called)
    # - ensure maintenance mode is on
    # - enable patroni as standby
    # - trigger deployment (will scale down keycloak, Kong Control Plane)
    # - wait for deployment to complete (Tekton Event ID)
    def initiate_standby(self, namespace: str, patroni_local_url: str, py_env: str, final_state: str):
        logger.info("initiate_standby")

        self.maintenance_on(namespace, py_env)

        if self.patroni['control'] == 'up' and len(self.patroni['concerns']) == 0 and self.patroni['leader']['role'] == 'standby_leader':
            logger.warn(
                "Patroni has no concerns and is already a Standby Leader, no further action")
            return None
        else:
            self.triggers.clear()

            scale_and_wait(namespace, 'statefulset',
                           config.get('statefulset_patroni'),
                           "app=%s" % config.get('statefulset_patroni'),
                           0, py_env)

            scale_and_wait(namespace, 'deployment',
                           config.get('deployment_kong_control_plane'),
                           config.get(
                               'deployment_kong_control_plane_label_selector'),
                           0, py_env)

            scale_and_wait(namespace, 'statefulset',
                           config.get('statefulset_keycloak'),
                           config.get('statefulset_keycloak_label_selector'),
                           0, py_env)

            self.update_patroni_spilo_env_vars(
                namespace, True, py_env)

            delete_pvc(namespace, 'storage-volume-patroni-spilo-0', py_env)
            delete_configmap(namespace, 'patroni-spilo-config', py_env)
            scale_and_wait(namespace, 'statefulset',
                           'patroni-spilo', "app=patroni-spilo", 1, py_env)

            logger.debug("Adding Future work to be triggered later...")
            return WaitFor().wait_until(self.patroni_has_no_standby_concerns).then_trigger(
                self.complete_standby, namespace, py_env, final_state)

    def patroni_has_no_standby_concerns(self):
        return self.patroni['control'] == 'up' and len(self.patroni['concerns']) == 0 and self.patroni['leader']['role'] == 'standby_leader'

    def complete_standby(self, namespace: str, py_env: str, final_state: str):
        logger.info("complete_standby starting")

        delete_pvc(namespace, 'storage-volume-patroni-spilo-1', py_env)
        delete_pvc(namespace, 'storage-volume-patroni-spilo-2', py_env)
        scale_and_wait(namespace, 'statefulset',
                       'patroni-spilo', "app=patroni-spilo", 3, py_env)

        self.triggers.clear()

        pipeline_event = trigger_tekton_build(config.get("tekton_trigger_url"),
                                              config.get("tekton_github_repo"),
                                              config.get("tekton_github_ref"),
                                              config.get("tekton_github_hmac_signature"))
        logger.info("Triggered tekton event %s" % pipeline_event['eventID'])
        self.pipeline['event_id'] = pipeline_event['eventID']
        self.pipeline['start_ts'] = datetime.datetime.now()
        self.pipeline['maintenance'] = True

        self.update_switchover_state(
            namespace, final_state, '', py_env)

    def initiate_maintenance(self, namespace: str, patroni_local_url: str, py_env: str):
        logger.info("Maintenance")

        scale(config.get('kube_health_namespace'), 'deployment',
              config.get('deployment_health_api'), 0, py_env)

        set_primary_cluster(patroni_local_url)

    def update_patroni_spilo_env_vars(self, namespace: str, standby: bool, py_env: str):
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

    def update_switchover_state(self, namespace: str, last_stable_state: str, transition: str, py_env: str):
        name = config['configmap_switchover']
        data = dict()
        if last_stable_state is not None:
            data['last_stable_state'] = last_stable_state
            data['last_stable_state_ts'] = datetime.datetime.now()
        if transition is not None:
            data['transition'] = transition
        update_configmap(namespace, name, py_env, dict(data=data))

    def maintenance_on(self, namespace: str, py_env: str):
        logger.debug("MAINTENANCE TURNING ON..")

        # Cycle maintenance page (clears out any connections there might be)
        restart_deployment(namespace, config.get(
            'deployment_keycloak_maintenance_page'), py_env)

        # Switch keycloak service to maintenance
        keycloak_service_block()

        # Turn on maintenance alert on Portal
        set_maintenance(config.get('maintenance_url'), True)

        logger.debug("MAINTENANCE ON - OK")

    def maintenance_off(self, namespace: str, py_env: str):
        logger.debug("MAINTENANCE TURNING OFF..")

        # Cycle maintenance page (clears out any connections there might be)
        restart_deployment(namespace, config.get(
            'deployment_keycloak_maintenance_page'), py_env)

        # Switch keycloak service to keycloak
        keycloak_service_flow()

        # Turn off maintenance alert on Portal
        set_maintenance(config.get('maintenance_url'), False)

        logger.debug("MAINTENANCE OFF - OK")

    def set_in_recovery(self, state: bool, py_env: str):
        if state:
            spec = {"in_recovery": "true"}
        else:
            spec = {"in_recovery": "false"}
        patch_secret(config.get("tekton_namespace"),
                     config.get("tekton_terraform_tfvars"), py_env, spec)

    def pick_params(self, list, keys):
        pairs = {}
        for item in list:
            if item['name'] in keys:
                pairs[item['name']] = item['value']
        return pairs
