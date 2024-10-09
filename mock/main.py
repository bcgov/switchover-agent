from fastapi import FastAPI, Response, Request, Body, status
import os
import sys
import json
import time
import logging
import uvicorn
from fastapi.responses import StreamingResponse
from config import config
from data.phases import phases
from mask import mask_utc_timestamps

app = FastAPI()

mocks = {}

CURR = os.path.dirname(os.path.realpath(__file__))

def get(base: str, path: str):
  logging.info("Open %s%s" % (base, path))
  f = open("%s%s" % (base, path))
  return json.load(f)

@app.put("/phase/{phase:str}")
def phase(request: Request, phase: str):
  logging.warning("PHASE %s", phase)
  for k in phases[phase].keys():
    logging.warning("  -- %-20s = %s", k, phases[phase][k])
    config[k] = phases[phase][k]
  config["activity"].append({"path":"PHASE_CHANGE/%s" % phase, "body": phases[phase] })
  return Response(status_code=status.HTTP_204_NO_CONTENT)

@app.put("/initiate/{event:str}")
def initiate(request: Request, event: str):
  if event == 'dns_lookup_error':
    config['dns'] = 'error.json'
  
  config["activity"].append({"path":"INITIATE/%s" % event })
  return Response(status_code=status.HTTP_204_NO_CONTENT)

@app.get("/activity")
def state(request: Request):
  return Response(content=json.dumps(mask_utc_timestamps(config["activity"]), indent=2), media_type="application/json")

@app.get("/k8s/{rest_of_path:path}")
async def k8s(request: Request, rest_of_path: str, watch: bool = False):
      # kubectl get configmaps -l app=switchover -l name=switchover-config -l env=local -n b8840c-tools -o json -v=9
      # kubectl get configmaps/switchover-state-local -n b8840c-tools -o json -v=10
      # kubectl get configmaps/switchover-state-local -n b8840c-tools -w -v=10
      # kubectl get services/keycloak -n b8840c-dev -w -v=10
      # kubectl get pipelineruns/aps-cicd-ocp-tkn-terraform-pipeline-deployment-b8840c-dev-7f7xk -n b8840c-tools  -v=10
      # kubectl get statefulsets/patroni-spilo -n b8840c-dev -w -v=10
  
      logging.warning("GET request /k8s/%s %s", rest_of_path, request)
      logging.warning("WATCH = %s", watch)
      
      def slow_iter(count: int, watch, rest_of_path: str):
        last = None
        for n in range(count):
          paths = {
            "api/v1/namespaces/000000-tools/configmaps": config['k8s.configmaps'],
            "apis/tekton.dev/v1beta1/namespaces/000000-tools/pipelineruns": config['k8s.pipelineruns'],
            "api/v1/namespaces/000000-dev/services": config['k8s.services'],
            "apis/apps/v1/namespaces/000000-dev/statefulsets": config['k8s.statefulsets']
          }
          
          file = paths[rest_of_path]
          if last != file:
            last = file
            body = get(CURR, "/data/k8s/%s" % file)
            if watch is False:
               logging.warning("Return LIST %s" % file)
               body = {
                  "items": [
                     body["object"]
                  ]
               }
            logging.warning("STREAM EVENT: %s %s %s", n, rest_of_path, "/data/k8s/%s" % config['k8s.configmaps'])
            yield "%s\n" % json.dumps(body)
          # else:
          #   logging.warning("SLOW ITER NOTHING NEW %s" % file)
          time.sleep(2)

      if watch:
        return StreamingResponse(slow_iter(10, watch, rest_of_path), media_type="application/json")
      else:
        return StreamingResponse(slow_iter(1, watch, rest_of_path), media_type="application/json")
      
      # mocks[rest_of_path] = { "query": dict(request.query_params) }
      # logging.error(json.dumps(mocks, indent=4))
      # return {}
  
# @app.put("/k8s/{rest_of_path:path}")
# async def k8s_put(request: Request, rest_of_path: str):
#       logging.info("PUT request %s %s",rest_of_path, request)

# @app.post("/k8s/{rest_of_path:path}")
# async def k8s_post(request: Request, rest_of_path: str):
#       logging.info("POST request %s %s",rest_of_path, request)

# @app.delete("/k8s/{rest_of_path:path}")
# async def k8s_delete(request: Request, rest_of_path: str):
#       logging.info("DELETE request %s %s",rest_of_path, request)

@app.patch("/k8s/{rest_of_path:path}")
async def k8s_patch(request: Request, rest_of_path: str):
    logging.warning("--")
    logging.warning("--")
    logging.warning("PATCH request %s %s",rest_of_path, request)
    # apis/apps/v1/namespaces/000000-tools/deployments/bcgov-health-api-local-generic-api/scale
    # apis/apps/v1/namespaces/000000-dev/deployments/keycloak-maintenance-redirect-generic-api
    # api/v1/namespaces/000000-dev/services/keycloak-http
    # api/v1/namespaces/000000-tools/configmaps/switchover-state-local
    # apis/apps/v1/namespaces/000000-dev/deployments/konghc-kong
    # apis/apps/v1/namespaces/000000-dev/statefulsets/patroni-spilo/scale
    body = await request.json()
    logging.warning("-- %s", json.dumps(body, indent=2))
    config["activity"].append({"path":rest_of_path, "method": "PATCH", "body": body })
    
    if rest_of_path == 'api/v1/namespaces/000000-tools/configmaps/switchover-state-local':
      if body['data']['transition'] == "":
        # Patching without a transition is when the work to transition has been completed
        # so here can do any correcting of mock state, such as what the DNS is expected to be
        if body['data']['last_stable_state'] == "active-passive":
          config['k8s.configmaps'] = "configmaps-active-passive.json"
          config['dns'] = "active.json"
        elif body['data']['last_stable_state'] == "golddr-primary":
          config['k8s.configmaps'] = "configmaps-golddr-primary.json"
          config['dns'] = "passive.json"
        elif body['data']['last_stable_state'] == "gold-standby":
          config['k8s.configmaps'] = "configmaps-gold-standby.json"
        elif body['data']['last_stable_state'] == "gold-standby-partial":
          config['k8s.configmaps'] = "configmaps-gold-standby-partial.json"
        else:
          logging.warning("-- DO NOTHING ON MOCK")
      else:
        if body['data']['transition'] == 'golddr-primary' and body['data']['maintenance'] is None:
          config['k8s.configmaps'] = "configmaps-transition-golddr-primary.json"
        elif body['data']['transition'] == 'gold-standby' and body['data']['maintenance'] is None:
          config['k8s.configmaps'] = "configmaps-transition-gold-standby.json"
        elif body['data']['transition'] == 'active-passive':
          config['k8s.configmaps'] = "configmaps-transition-active-passive.json"
      return Response(status_code=status.HTTP_204_NO_CONTENT)
    elif rest_of_path == 'apis/apps/v1/namespaces/000000-dev/statefulsets/patroni-spilo/scale':
      patroni_config = get(CURR, "/data/patroni/%s" % config['patroni.config'])
      
      if body['spec']['replicas'] == 0:
        config['k8s.statefulsets'] = 'statefulsets-patroni-0.json'
      elif body['spec']['replicas'] == 1:
        config['k8s.statefulsets'] = 'statefulsets-patroni-1.json'
        if 'standby_cluster' in patroni_config:
          config['patroni.cluster'] = 'cluster-standby-1.json'
        else:
          config['patroni.cluster'] = 'cluster-leader-1.json'
      elif body['spec']['replicas'] == 3:
        config['k8s.statefulsets'] = 'statefulsets.json'
        if 'standby_cluster' in patroni_config:
          config['patroni.cluster'] = 'cluster-standby-3.json'
        else:
          config['patroni.cluster'] = 'cluster.json'
        
    elif rest_of_path == 'api/v1/namespaces/000000-dev/configmaps/patroni-spilo-env-vars':
      if body['data']['STANDBY_HOST'] != '':
        config['patroni.config'] = 'config-standby.json'
      else:
        config['patroni.config'] = 'config.json'
        
    return Response(status_code=status.HTTP_204_NO_CONTENT)

@app.delete("/k8s/{rest_of_path:path}")
async def delete(request: Request, rest_of_path: str):
    logging.info("DELETE request %s %s",rest_of_path, request)

    body = None
    #body = await request.json()
    #logging.warning("-- %s", json.dumps(body, indent=2))
    config["activity"].append({"path":rest_of_path, "method": "DELETE", "body": body })

    return Response(status_code=status.HTTP_204_NO_CONTENT)

@app.patch("/patroni/config")
async def patroni_patch(request: Request):
    logging.info("PATCH request /patroni/config")

    body = await request.json()
    logging.warning("-- %s", json.dumps(body, indent=2))
    config["activity"].append({"path":"/patroni/config", "method": "PATCH", "body": body })

    if body['standby_cluster'] is None:
      config['patroni.cluster'] = 'cluster.json'
    else:
      config['patroni.cluster'] = 'cluster-standby-3.json'

    return Response(status_code=status.HTTP_204_NO_CONTENT)

@app.get("/patroni/{rest_of_path:path}")
def patroni(request: Request, rest_of_path: str):
    logging.info("GET request /patroni/%s",rest_of_path)

    if rest_of_path == 'config':
      logging.info("Patroni Config %s", config['patroni.config'])
      f = open("%s/data/patroni/%s" % (CURR, config['patroni.config']))
      return json.loads(f.read())
    
    elif rest_of_path == 'cluster':
      logging.info("Patroni Cluster %s", config['patroni.cluster'])
      f = open("%s/data/patroni/%s" % (CURR, config['patroni.cluster']))
      return json.loads(f.read())

    else:
      return Response(content="error")

@app.put("/maintenance/{rest_of_path:path}")
def maintenance(request: Request, rest_of_path: str):
    logging.info("PUT request %s %s",rest_of_path, request)
    
    config["activity"].append({"path":"MAINTENANCE", "method": "PUT", "body": rest_of_path })

    return {}

@app.get("/dns")
def dns(request: Request):
    logging.info("GET request /dns %s", request)
    
    return get(CURR, "/data/dns/%s" % config['dns'])

@app.post("/tekton")
async def tekton(request: Request):
    logging.info("POST request %s", request)
    
    body = await request.json()
    config["activity"].append({"path":"TEKTON/TRIGGER", "method": "POST", "body": body })

    return { "eventID": "0000-0000-0000"}
  

# Not addressed yet
# - update configmap envvars for patroni
# - trigger a tekton deployment
# - restart deployments / statefulsets
# - scale deployments / statefulsets


# @app.put("/{rest_of_path:path}")
# def put(request: Request, rest_of_path: str):
#     logging.info("PUT request %s %s",rest_of_path, request)
#     mocks[rest_of_path] = { "query": dict(request.query_params) }
#     logging.info(json.dumps(mocks, indent=4))
#     return Response(content="hi", media_type="text/plain")


# @app.post("/{rest_of_path:path}")
# def post(request: Request, rest_of_path: str):
#     logging.info("POST request %s %s",rest_of_path, request)
#     mocks[rest_of_path] = { "query": dict(request.query_params) }
#     logging.info(json.dumps(mocks, indent=4))
#     return Response(content="hi", media_type="text/plain")

def fastapi():
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level='warning')

log_level = os.getenv('LOG_LEVEL', 'DEBUG')


logging.basicConfig(
    stream=sys.stdout,
    level=logging.getLevelName(log_level),
    format='%(asctime)s [%(levelname)-5s] %(name)-20s %(message)s')

if __name__ == '__main__':
    fastapi()
