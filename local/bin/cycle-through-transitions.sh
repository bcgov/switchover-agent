#!/bin/bash

set -e

curl -v http://localhost:6664/phase/transition-to-golddr-primary -X PUT
sleep 15
curl -v http://localhost:6664/phase/transition-to-gold-standby -X PUT
sleep 15
curl -v http://localhost:6664/phase/transition-to-active-passive -X PUT
sleep 15

# Retrieve Active Mock Activity
curl -v http://127.0.0.1:6664/activity -X GET > activity_test_a.json

# Retrieve Passive Mock Activity
curl -v http://127.0.0.1:6665/activity -X GET > activity_test_p.json

diff baseline/activity_a.json activity_test_a.json
diff baseline/activity_p.json activity_test_p.json
