#!/usr/bin/env bash
# Runs this app's tests on a local bench in Docker. The first run builds the bench, which takes some
# minutes; later runs reuse it. Extra arguments go to "bench run-tests", for example --module NAME.
set -euo pipefail
cd "$(dirname "$0")"

docker compose up -d
# The bench volume starts out owned by root.
docker compose exec -T -u root bench chown frappe:frappe /home/frappe/work

# Copy the app into the container. Docker Desktop on macOS shows a bind-mounted folder with stale
# directory listings, and Python then misses new files, so the app is copied for each run instead.
docker compose exec -T bench bash -c 'rm -rf /home/frappe/work/activationlab && mkdir /home/frappe/work/activationlab'
COPYFILE_DISABLE=1 tar --no-xattrs --no-mac-metadata -C .. --exclude=__pycache__ --exclude=.DS_Store -cf - . |
	docker compose exec -T bench tar -C /home/frappe/work/activationlab -xf -

docker compose exec -T bench bash -lc 'bash /home/frappe/work/activationlab/dev/setup-bench.sh'
docker compose exec -T bench bash -lc "cd /home/frappe/work/frappe-bench && bench --site test.localhost run-tests --app activationlab $*"

# The learner script and stylesheet tests run in Node with jsdom, a simulated browser.
docker compose exec -T bench bash -lc '
	cd /home/frappe/work && mkdir -p node-tools &&
	{ [ -d node-tools/node_modules/jsdom ] || npm install --silent --no-audit --no-fund --prefix node-tools jsdom@26.1.0; } &&
	NODE_PATH=/home/frappe/work/node-tools/node_modules node --test activationlab/activationlab/tests/learner.test.cjs activationlab/activationlab/tests/learner-css.test.cjs'
