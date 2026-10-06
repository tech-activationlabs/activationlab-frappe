"""The Activation Lab brand where the Learning app has its own and no setting changes it: the manifest of
the installable app, and the iPhone home-screen icon and launch screens of the /lms pages.

The icons are in public/images and are served at /assets/activationlab/images/.
"""

import json
import re

import frappe
from werkzeug.wrappers import Response

ICONS = "/assets/activationlab/images"

# The website's own description of the school, from the meta description of its home page.
DESCRIPTION = (
	"Online courses that teach managers and leaders who are not engineers how to train AI agents to do real work."
)

TOUCH_ICON = re.compile(r'<link\s+rel="apple-touch-icon"[^>]*>')
LAUNCH_SCREEN = re.compile(r'\s*<link\s+rel="apple-touch-startup-image"[^>]*>')


@frappe.whitelist(allow_guest=True)
def get_pwa_manifest():
	"""Stands in for lms.lms.api.get_pwa_manifest (hooks.py, override_whitelisted_methods). The fields are
	the Learning app's; the icons and the description are Activation Lab's."""
	from lms.lms.utils import get_lms_route

	title = frappe.db.get_single_value("Website Settings", "app_name") or "Activation Lab"
	route = get_lms_route()
	manifest = {
		"id": route,
		"name": title,
		"short_name": title,
		"description": DESCRIPTION,
		"start_url": route,
		"scope": route,
		"display": "standalone",
		"orientation": "portrait",
		"theme_color": "#FFFFFF",
		"background_color": "#FFFFFF",
		"icons": [
			{"src": f"{ICONS}/activation-lab-mark-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
			{"src": f"{ICONS}/activation-lab-mark-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
			# A platform may cut a maskable icon to a circle or a squircle: the mark sits in the central 80 %.
			{"src": f"{ICONS}/activation-lab-app-icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
			{"src": f"{ICONS}/activation-lab-app-icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
		],
	}
	return Response(json.dumps(manifest), status=200, content_type="application/manifest+json")


def brand_icons(html: str) -> str:
	"""The brand's iPhone home-screen icon in place of the Learning app's. The Learning app's launch screens
	show its own icon, so they go; without them iOS shows the theme colour while the app opens."""
	html = TOUCH_ICON.sub(f'<link rel="apple-touch-icon" href="{ICONS}/activation-lab-mark-180.png">', html, count=1)
	return LAUNCH_SCREEN.sub("", html)
