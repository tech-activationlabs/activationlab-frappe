"""The Learning app's pages at /lms, served through the Learning app's own template with three changes:

1. A signed-out visitor gets a short page that sends the browser to the sign-up page, which comes
   back to the same address afterwards. The Learning app sends them to /login with no way back.
   The short page carries the Learning app's title, description and image of the address, so that a
   link shared on LinkedIn or in Slack still shows the course, badge or profile it points to.
2. /lms/billing/course/NAME goes to the Stripe checkout of this app. The Learning app's billing page
   leads to the Payments app's card form, which Stripe no longer accepts.
3. The learner styles and script of this app go into the page head. The Learning page is a complete
   document, so Frappe adds no site-wide head HTML or website script to it.

Frappe asks each page renderer of the installed apps, in install order, before its own renderers.
The Learning app's renderer handles SCORM files only, so this one gets the /lms pages.
"""

import hashlib
import json
import re
from functools import lru_cache
from html import escape
from urllib.parse import quote

import frappe
from frappe.utils import get_url
from frappe.website.page_renderers.base_renderer import BaseRenderer
from frappe.website.page_renderers.not_found_page import NotFoundPage
from frappe.website.page_renderers.template_page import TemplatePage
from werkzeug.utils import redirect
from werkzeug.wrappers import Response

from activationlab.branding import brand_icons
from activationlab.utils import BUY_PATH, buy_url, is_staff, lms_path, login_url

LMS_ENDPOINT = "_lms"
BILLING_COURSE = re.compile(r"^billing/course/([^/]+)/?$")
FONTS_URL = (
	"https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1"
	"&family=Inter:opsz,wght@14..32,400..700&display=swap"
)
MARKER = "data-activationlab"


class LMSPage(BaseRenderer):
	def can_render(self):
		return self.path == LMS_ENDPOINT

	def render(self):
		app_path = (frappe.form_dict.get("app_path") or "").strip("/")

		if frappe.session.user == "Guest" and not guest_access_allowed():
			return sign_in_page(app_path)

		billing = BILLING_COURSE.match(app_path)
		if billing:
			return redirect(buy_url(billing.group(1)), code=302)

		page = TemplatePage(self.path, self.http_status_code)
		if not page.can_render():
			return NotFoundPage(self.path, self.http_status_code).render()
		response = page.render()
		add_learner_tags(response)
		return response


def guest_access_allowed() -> bool:
	return bool(frappe.db.get_single_value("LMS Settings", "allow_guest_access"))


def sign_in_page(app_path: str):
	"""For a signed-out visitor: a page that sends the browser on to sign-up and back to this address.
	Link previews read its title, description and image without running the script."""
	target = login_url(current_address())
	meta = preview_meta(app_path)
	title, description, image = (
		escape(str(meta.get(key) or ""), quote=True) for key in ("title", "description", "image")
	)
	link = escape(target, quote=True)
	# The address goes into a <script>, so a "</" in it must not close the script.
	script_target = json.dumps(target).replace("</", "<\\/")
	html = (
		'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
		'<meta name="viewport" content="width=device-width, initial-scale=1">'
		f"<title>{title}</title>"
		f'<meta name="description" content="{description}">'
		f'<meta property="og:title" content="{title}">'
		f'<meta property="og:description" content="{description}">'
		f'<meta property="og:image" content="{image}">'
		'<meta name="twitter:card" content="summary_large_image">'
		f'<meta name="twitter:title" content="{title}">'
		f'<meta name="twitter:description" content="{description}">'
		f'<meta name="twitter:image" content="{image}">'
		f"<script>window.location.replace({script_target});</script>"
		"</head><body>"
		f"<h1>{title}</h1><p>{description}</p>"
		f'<p><a href="{link}">Sign in to continue</a></p>'
		"</body></html>"
	)
	response = Response(html, status=200, mimetype="text/html")
	# Never kept: after sign-in the same address must reach the Learning app, not this page again.
	response.headers["Cache-Control"] = "no-store"
	return response


def preview_meta(app_path: str) -> dict:
	"""The Learning app's title, description and image for an address, as its own page has them. An
	unpublished course shows only the site's name. Image addresses are made absolute for crawlers."""
	title = frappe.db.get_single_value("Website Settings", "app_name") or "Frappe Learning"
	favicon = frappe.db.get_single_value("Website Settings", "favicon") or "/assets/lms/frontend/favicon.png"
	parts = app_path.split("/")
	if (
		parts[0] == "courses"
		and len(parts) > 1
		and not frappe.db.get_value("LMS Course", parts[1], "published")
	):
		app_path = ""
	try:
		from lms.www._lms import get_meta

		meta = dict(get_meta(app_path, title, favicon))
	except Exception:
		meta = {"title": title, "image": favicon}
	if str(meta.get("image") or "").startswith("/"):
		meta["image"] = get_url(meta["image"])
	return meta


def current_address() -> str:
	"""The path and query of this request, URL-encoded as the browser sent them."""
	request = frappe.local.request
	address = quote(request.path)
	query = request.query_string.decode()
	return f"{address}?{query}" if query else address


def add_learner_tags(response):
	"""Put the learner styles and script before </head>, once."""
	html = response.get_data(as_text=True)
	if "</head>" not in html or MARKER in html:
		return
	html = brand_icons(html)
	response.set_data(html.replace("</head>", learner_tags() + "</head>", 1))


def learner_tags() -> str:
	# The data goes into a <script>, so a "</" inside a course name must not close it.
	config = json.dumps(learner_config(), separators=(",", ":")).replace("</", "<\\/")
	return "".join(
		(
			'<link rel="preconnect" href="https://fonts.googleapis.com">',
			'<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>',
			f'<link rel="stylesheet" href="{FONTS_URL.replace("&", "&amp;")}">',
			f'<link rel="stylesheet" href="{asset_url("css/learner.css")}">',
			f"<script {MARKER}>window.activationlab = {config};</script>",
			f'<script src="{asset_url("js/learner.js")}"></script>',
		)
	)


def learner_config() -> dict:
	"""What the learner script needs to know about the courses and the signed-in user."""
	user = frappe.session.user
	return {
		"lmsPath": lms_path(),
		"buyUrl": BUY_PATH,
		"staff": is_staff(user),
		"paidCourses": frappe.get_all(
			"LMS Course", filters={"published": 1, "paid_course": 1}, pluck="name", order_by="name"
		),
		"enrolled": []
		if user == "Guest"
		else frappe.get_all("LMS Enrollment", filters={"member": user}, pluck="course", order_by="course"),
	}


def asset_url(path: str) -> str:
	return f"/assets/activationlab/{path}?v={asset_version(path)}"


@lru_cache(maxsize=8)
def asset_version(path: str) -> str:
	"""A short hash of the file, so that a browser fetches it again after each change."""
	with open(frappe.get_app_path("activationlab", "public", *path.split("/")), "rb") as f:
		return hashlib.sha256(f.read()).hexdigest()[:10]
