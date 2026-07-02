#!/bin/bash

set -e

curl -v http://localhost:6664/phase/transition-to-golddr-primary -X PUT
sleep 15
curl -v http://localhost:6664/phase/transition-to-gold-standby -X PUT
sleep 15
curl -v http://localhost:6664/phase/transition-to-active-passive -X PUT

# Passive finishes last; wait for active-passive + final maintenance (max 90s).
for i in $(seq 1 90); do
  p=$(curl -sf http://127.0.0.1:6665/activity) || { sleep 1; continue; }
  echo "$p" | grep -q '"last_stable_state": "active-passive"' \
    && echo "$p" | tail -4 | grep -q 'maintenance/true' && break
  sleep 1
done
if [ "$i" -eq 90 ]; then
  echo "Timed out waiting for passive mock to finish active-passive" >&2
  exit 1
fi

curl -v http://127.0.0.1:6664/activity -X GET > activity_test_a.json
curl -v http://127.0.0.1:6665/activity -X GET > activity_test_p.json

diff baseline/activity_a.json activity_test_a.json
diff baseline/activity_p.json activity_test_p.json
