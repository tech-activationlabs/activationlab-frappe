#!/usr/bin/env bash
# Publishes this app folder, as committed on the current branch of demo_repo, to the GitHub repository
# that Frappe Cloud installs it from. The GitHub repository is public, so it gets the files only: one
# commit per publish, authored by Activation Lab, with no history of demo_repo. Safe to run twice: it
# commits only when the files changed.
# Usage: frappe/apps/activationlab/dev/publish.sh
set -euo pipefail

REMOTE="${ACTIVATIONLAB_APP_REMOTE:-git@github-activationlab:tech-activationlabs/activationlab-frappe.git}"
BRANCH=main
APP_DIR=frappe/apps/activationlab

ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
SOURCE="$(git -C "$ROOT" rev-parse --short HEAD)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

if git ls-remote --exit-code --heads "$REMOTE" "$BRANCH" >/dev/null 2>&1; then
	git clone -q --branch "$BRANCH" "$REMOTE" "$WORK/repo"
else
	git init -q -b "$BRANCH" "$WORK/repo"
	git -C "$WORK/repo" remote add origin "$REMOTE"
fi

# Replace the files with the committed app folder. Uncommitted changes are left out on purpose.
find "$WORK/repo" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
git -C "$ROOT" archive --format=tar HEAD:"$APP_DIR" | tar -x -C "$WORK/repo" -f -

cd "$WORK/repo"
git add -A
if git diff --cached --quiet; then
	echo "No change to publish: $REMOTE $BRANCH already holds the app of demo_repo $SOURCE."
	exit 0
fi
git -c user.name="Activation Lab" -c user.email="tech@activationlab.ai" commit -q -m "Activation Lab app from demo_repo $SOURCE"
git push -q origin "$BRANCH"
echo "Published the app of demo_repo $SOURCE to $REMOTE $BRANCH as $(git rev-parse --short HEAD)."
