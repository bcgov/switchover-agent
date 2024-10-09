phases = {
  "active-passive": {
   "dns": "active.json",
    "k8s.configmaps": "configmaps.json",
    "k8s.pipelineruns": "pipelineruns.json",
    "patroni.cluster": "cluster.json",
    "patroni.config": "config.json"
  },
  "transition-to-golddr-primary": {
   "dns": "active.json",
    "k8s.configmaps": "configmaps-transition-golddr-primary.json",
    "k8s.pipelineruns": "pipelineruns.json",
    "patroni.cluster": "cluster.json",
    "patroni.config": "config.json"
  },
  "transition-to-gold-standby": {
   "dns": "passive.json",
    "k8s.configmaps": "configmaps-transition-gold-standby.json",
    "k8s.pipelineruns": "pipelineruns.json",
    "patroni.cluster": "cluster.json",
    "patroni.config": "config.json"
  },
  "transition-to-active-passive": {
   "dns": "passive.json",
    "k8s.configmaps": "configmaps-transition-active-passive.json",
    "k8s.pipelineruns": "pipelineruns.json",
    "patroni.cluster": "cluster-standby-3.json",
    "patroni.config": "config-standby.json"
  },  
}
