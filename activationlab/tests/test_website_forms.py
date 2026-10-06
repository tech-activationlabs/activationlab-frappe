"""The website's forms may read this site's reply, at the two addresses they call and nowhere else."""

import frappe
from frappe.app import set_cors_headers
from frappe.tests import IntegrationTestCase
from frappe.utils import set_request
from werkzeug.wrappers import Response

from activationlab.website_forms import allow_the_website

WEBSITE = "https://activationlab-website-3h4q5n5voq-ue.a.run.app"
ENTRY = "/api/method/frappe.website.doctype.web_form.web_form.accept"


class TestWebsiteForms(IntegrationTestCase):
	def setUp(self):
		self.request = getattr(frappe.local, "request", None)

	def tearDown(self):
		frappe.local.request = self.request
		if hasattr(frappe.local, "allow_cors"):
			del frappe.local.allow_cors
		frappe.local.conf.pop("activationlab_website_origins", None)

	def reply(self, path, origin, method="POST"):
		"""The CORS headers of the reply to one request, as Frappe writes them."""
		if hasattr(frappe.local, "allow_cors"):
			del frappe.local.allow_cors
		headers = {"Origin": origin} if origin else {}
		set_request(path=path, method=method, headers=headers)
		allow_the_website()
		response = Response()
		set_cors_headers(response)
		return response.headers

	def test_the_website_reads_the_reply_to_an_entry(self):
		headers = self.reply(ENTRY, WEBSITE)
		self.assertEqual(headers.get("Access-Control-Allow-Origin"), WEBSITE)
		self.assertEqual(headers.get("Vary"), "Origin")

	def test_the_website_reads_the_reply_to_its_check(self):
		headers = self.reply("/api/method/ping", WEBSITE, method="GET")
		self.assertEqual(headers.get("Access-Control-Allow-Origin"), WEBSITE)

	def test_every_address_of_the_website_is_named(self):
		for origin in (
			"https://activationlab-website-446881363600.us-east1.run.app",
			"https://activationlab.ai",
			"https://www.activationlab.ai",
		):
			self.assertEqual(self.reply(ENTRY, origin).get("Access-Control-Allow-Origin"), origin)

	def test_another_site_is_not_named(self):
		self.assertIsNone(self.reply(ENTRY, "https://example.org").get("Access-Control-Allow-Origin"))

	def test_other_addresses_of_this_site_stay_closed_to_the_website(self):
		for path in ("/api/resource/User", "/api/method/frappe.auth.get_logged_user", "/lms"):
			self.assertIsNone(self.reply(path, WEBSITE, method="GET").get("Access-Control-Allow-Origin"))

	def test_a_request_without_an_origin_is_left_alone(self):
		self.assertIsNone(self.reply(ENTRY, None).get("Access-Control-Allow-Origin"))

	def test_the_site_config_can_add_an_address(self):
		frappe.local.conf["activationlab_website_origins"] = ["https://preview.activationlab.ai"]
		origin = "https://preview.activationlab.ai"
		self.assertEqual(self.reply(ENTRY, origin).get("Access-Control-Allow-Origin"), origin)
