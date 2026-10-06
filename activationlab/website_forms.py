"""The public website's forms send their entries to this site from another web address. A browser lets
the website read the reply only when this site names the website's address in it. This names the
website's address for the two addresses the forms call, and for nothing else, so a visitor sees when
the site refuses an entry, for example for a mistyped email address."""

import frappe

# The website's addresses. A site can add more in its site config, as the list
# activationlab_website_origins.
WEBSITE_ORIGINS = (
	"https://activationlab-website-3h4q5n5voq-ue.a.run.app",
	"https://activationlab-website-446881363600.us-east1.run.app",
	"https://activationlab.ai",
	"https://www.activationlab.ai",
)

# The addresses the forms call: a check that the reply can be read, and the entry itself.
FORM_PATHS = (
	"/api/method/ping",
	"/api/method/frappe.website.doctype.web_form.web_form.accept",
)


def website_origins() -> list[str]:
	extra = frappe.conf.get("activationlab_website_origins") or []
	if isinstance(extra, str):
		extra = [extra]
	return [*WEBSITE_ORIGINS, *extra]


def allow_the_website():
	"""A before_request hook. Frappe writes the headers from frappe.local.allow_cors when it sends the
	reply, error replies included."""
	request = getattr(frappe.local, "request", None)
	if not request or request.path not in FORM_PATHS:
		return
	origin = request.headers.get("Origin")
	if origin and origin in website_origins():
		frappe.local.allow_cors = [origin]
