#!/usr/bin/env bash
# Builds a local bench inside the "bench" container of dev/docker-compose.yml: Frappe, Payments and
# Learning at the commits that run on the course platform, this app, and the site test.localhost.
# Safe to run twice: each step checks for its result first. Front-end assets are not built.
set -euo pipefail

FRAPPE_SHA=aba24d9bf4caa65a2cc9277c1e771739d41165b1
PAYMENTS_SHA=cca07d9f9392e2ea0e521c5975151db9e4b6c321
LMS_SHA=87168fc7b2f24559e474ea4702cf1aa63b0db4e8

WORK=/home/frappe/work
BENCH=$WORK/frappe-bench
SITE=test.localhost
DB_ROOT_PASSWORD=root

# Fetch one app at one commit into a local repository with a branch named main.
fetch() {
	local name=$1 sha=$2 dir=$WORK/src/$1
	if [ "$(git -C "$dir" rev-parse HEAD 2>/dev/null || true)" = "$sha" ]; then return; fi
	rm -rf "$dir"
	git init -q -b main "$dir"
	git -C "$dir" fetch -q --depth 1 "https://github.com/frappe/$name" "$sha"
	git -C "$dir" checkout -q -B main FETCH_HEAD
}

# The MariaDB client in the bench image asks for TLS, which the local database does not offer.
if [ ! -f "$HOME/.my.cnf" ]; then printf '[client]\nskip-ssl\n' > "$HOME/.my.cnf"; fi

mkdir -p "$WORK/src"
fetch frappe "$FRAPPE_SHA"
fetch payments "$PAYMENTS_SHA"
fetch lms "$LMS_SHA"

if [ ! -d "$BENCH/apps/frappe" ]; then
	cd "$WORK"
	bench init --frappe-path "$WORK/src/frappe" --frappe-branch main \
		--skip-redis-config-generation --skip-assets --no-procfile --no-backups frappe-bench
fi

cd "$BENCH"
bench set-mariadb-host mariadb
bench set-redis-cache-host redis://redis:6379
bench set-redis-queue-host redis://redis:6379
bench set-redis-socketio-host redis://redis:6379

# Add an app to the bench's list of apps, on a line of its own.
list_app() {
	grep -qx "$1" sites/apps.txt && return
	if [ -n "$(tail -c1 sites/apps.txt)" ]; then echo >> sites/apps.txt; fi
	echo "$1" >> sites/apps.txt
}

# Payments and Learning go in as editable packages, without their Node packages.
for app in payments lms; do
	if [ ! -d "apps/$app" ]; then git clone -q "$WORK/src/$app" "apps/$app"; fi
	list_app "$app"
done
# test.sh copies this app to $WORK/activationlab before each run.
ln -sfn "$WORK/activationlab" apps/activationlab
list_app activationlab
for app in payments lms activationlab; do
	uv pip install -q -e "apps/$app" --python env/bin/python
done

# Frappe's own pages, such as /login and the 404 page, need Frappe's built assets.
if [ ! -f sites/assets/assets.json ]; then bench build --app frappe; fi

# The Learning page template is a build output. A copy of its source with the boot data
# that the build adds stands in for it, so that /lms renders here as on the platform.
LMS_TEMPLATE=apps/lms/lms/www/_lms.html
if [ ! -f "$LMS_TEMPLATE" ]; then
	sed 's#</body>#<script>{% for key in boot %}window["{{ key }}"] = {{ boot[key] | tojson }};{% endfor %}</script></body>#' \
		apps/lms/frontend/index.html > "$LMS_TEMPLATE"
fi

if [ ! -f "sites/$SITE/.created" ]; then
	bench new-site "$SITE" --force --db-root-password "$DB_ROOT_PASSWORD" --admin-password admin \
		--mariadb-user-host-login-scope=%
	touch "sites/$SITE/.created"
fi
bench --site "$SITE" set-config allow_tests true
for app in payments lms activationlab; do
	bench --site "$SITE" list-apps | grep -q "^$app" || bench --site "$SITE" install-app "$app"
done
bench --site "$SITE" migrate
bench --site "$SITE" list-apps
