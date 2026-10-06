"""The /lms pages: the sign-in redirect, the billing redirect and the learner tags."""

import json
import os
import re
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import set_request
from frappe.website.serve import get_response
from werkzeug.wrappers import Response

from activationlab import lms_page
from activationlab.tests.utils import emulated_transactions, make_course, make_user

LEARNER = "al-page-learner@example.com"

# The shape of the Learning app's built page, as the platform serves it: a complete document whose
# head ends with the app's module script and stylesheet.
LEARNING_PAGE = (
	'<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8" /><title>Learning</title>'
	'<script type="module" crossorigin src="/assets/lms/frontend/assets/index.js"></script>'
	'<link rel="stylesheet" crossorigin href="/assets/lms/frontend/assets/index.css">'
	'<script id="vite-plugin-pwa:register-sw" src="/assets/lms/frontend/registerSW.js"></script></head>'
	'<body><div id="app"></div><script>window["lms_path"] = "lms";</script></body></html>'
)


class FakeTemplatePage:
	can_render_result = True

	def __init__(self, path, http_status_code=None):
		self.path = path

	def can_render(self):
		return self.can_render_result

	def render(self):
		return Response(LEARNING_PAGE, mimetype="text/html")


class TestLMSPage(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.transactions = emulated_transactions()
		self.transactions.__enter__()
		make_user(LEARNER, "Page learner")
		self.paid = make_course("Activation Lab page course")
		self.owned = make_course("Activation Lab owned course")
		frappe.get_doc({"doctype": "LMS Enrollment", "member": LEARNER, "course": self.owned}).insert(
			ignore_permissions=True
		)
		frappe.db.set_single_value("LMS Settings", "allow_guest_access", 0)
		frappe.db.commit()
		self.template = patch("activationlab.lms_page.TemplatePage", FakeTemplatePage)
		self.template.start()

	def tearDown(self):
		self.template.stop()
		self.transactions.__exit__(None, None, None)
		frappe.set_user("Administrator")
		frappe.local.request = None
		frappe.db.rollback()

	def get(self, path, user="Guest", query_string=""):
		frappe.set_user(user)
		frappe.local.response = frappe._dict()
		set_request(method="GET", path=path, query_string=query_string)
		try:
			return get_response()
		finally:
			frappe.set_user("Administrator")

	def config_of(self, html):
		match = re.search(r"<script data-activationlab>window\.activationlab = (.*?);</script>", html)
		self.assertIsNotNone(match, html)
		return json.loads(match.group(1))

	def sign_in_target(self, response):
		"""The address that the guest page sends the browser to, after checking the page itself."""
		html = response.get_data(as_text=True)
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.headers["Cache-Control"], "no-store")
		self.assertNotIn("data-activationlab", html)
		match = re.search(r'<script>window\.location\.replace\("(.*?)"\);</script>', html)
		self.assertIsNotNone(match, html)
		self.assertIn(f'<a href="{match.group(1)}">Sign in to continue</a>', html)
		return match.group(1)

	def preview(self, response):
		"""The link preview tags of a page, by property."""
		return dict(
			re.findall(r'<meta property="og:(\w+)" content="([^"]*)">', response.get_data(as_text=True))
		)

	def test_guest_signs_up_and_comes_back_to_the_same_address(self):
		response = self.get(f"/lms/courses/{self.paid}/learn/1-3", query_string="tab=notes")
		self.assertEqual(
			self.sign_in_target(response),
			f"/login?redirect-to=/lms/courses/{self.paid}/learn/1-3%3Ftab%3Dnotes#signup",
		)

	def test_guest_at_the_root_comes_back_to_the_root(self):
		self.assertEqual(self.sign_in_target(self.get("/lms")), "/login?redirect-to=/lms#signup")

	def test_shared_course_link_shows_the_course(self):
		preview = self.preview(self.get(f"/lms/courses/{self.paid}"))
		self.assertEqual(preview["title"], "Activation Lab page course")
		self.assertEqual(preview["description"], "A course for the activationlab tests.")
		self.assertTrue(preview["image"].startswith("http"), preview)

	def test_shared_link_of_an_unpublished_course_shows_only_the_site(self):
		frappe.db.set_value("LMS Course", self.paid, "published", 0)
		preview = self.preview(self.get(f"/lms/courses/{self.paid}"))
		self.assertNotIn("Activation Lab page course", preview["title"])

	def test_guest_sees_the_app_when_guest_access_is_on(self):
		frappe.db.set_single_value("LMS Settings", "allow_guest_access", 1)
		response = self.get("/lms/courses")
		self.assertEqual(response.status_code, 200)
		config = self.config_of(response.get_data(as_text=True))
		self.assertEqual((config["staff"], config["enrolled"]), (False, []))

	def test_billing_address_goes_to_the_checkout(self):
		response = self.get(f"/lms/billing/course/{self.paid}", user=LEARNER)
		self.assertEqual(response.status_code, 302)
		self.assertEqual(
			response.headers["Location"], f"/api/method/activationlab.checkout.buy?course={self.paid}"
		)

	def test_guest_billing_address_signs_up_first(self):
		response = self.get(f"/lms/billing/course/{self.paid}")
		self.assertEqual(
			self.sign_in_target(response), f"/login?redirect-to=/lms/billing/course/{self.paid}#signup"
		)

	def test_other_billing_addresses_stay_with_the_app(self):
		response = self.get(f"/lms/billing/certificate/{self.paid}", user=LEARNER)
		self.assertEqual(response.status_code, 200)

	def test_learner_page_gets_the_tags_once_in_the_head(self):
		response = self.get(f"/lms/courses/{self.paid}", user=LEARNER)
		html = response.get_data(as_text=True)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(html.count("data-activationlab"), 1)
		head = html.split("</head>")[0]
		self.assertIn("https://fonts.googleapis.com/css2?family=Instrument+Serif", head)
		self.assertRegex(
			head, r'<link rel="stylesheet" href="/assets/activationlab/css/learner\.css\?v=\w{10}">'
		)
		self.assertRegex(head, r'<script src="/assets/activationlab/js/learner\.js\?v=\w{10}"></script>')
		# A plain script runs while the page is read, before the app's module script, which waits for
		# the whole page. So the history hooks are in place before the app's router starts.
		self.assertNotRegex(head, r"<script[^>]*(defer|async|type=\"module\")[^>]*learner\.js")
		# After the app's stylesheet, so that rules of equal weight win.
		self.assertLess(head.index("frontend/assets/index.css"), head.index("activationlab/css/learner.css"))
		self.assertEqual(int(response.headers["Content-Length"]), len(response.get_data()))

		config = self.config_of(html)
		self.assertEqual(config["lmsPath"], "lms")
		self.assertEqual(config["buyUrl"], "/api/method/activationlab.checkout.buy")
		self.assertFalse(config["staff"])
		self.assertIn(self.paid, config["paidCourses"])
		self.assertEqual(config["enrolled"], [self.owned])

	def test_staff_are_marked(self):
		html = self.get("/lms/courses", user="Administrator").get_data(as_text=True)
		self.assertTrue(self.config_of(html)["staff"])

	def test_unpublished_courses_are_not_listed_for_sale(self):
		frappe.db.set_value("LMS Course", self.paid, "published", 0)
		html = self.get("/lms/courses", user=LEARNER).get_data(as_text=True)
		self.assertNotIn(self.paid, self.config_of(html)["paidCourses"])

	def test_config_cannot_close_its_script(self):
		with patch("activationlab.lms_page.learner_config", return_value={"name": "</script><b>"}):
			tags = lms_page.learner_tags()
		self.assertNotIn("</script><b>", tags)
		self.assertIn("<\\/script><b>", tags)

	def test_other_pages_are_left_alone(self):
		response = self.get("/login")
		self.assertEqual(response.status_code, 200)
		self.assertNotIn("data-activationlab", response.get_data(as_text=True))

	def test_missing_learning_template_is_not_found(self):
		with patch.object(FakeTemplatePage, "can_render_result", False):
			response = self.get("/lms/courses", user=LEARNER)
		self.assertEqual(response.status_code, 404)

	def test_addresses_survive_a_change_of_the_learning_helpers(self):
		from activationlab import utils

		with (
			patch("lms.lms.utils.get_lms_route", side_effect=AttributeError("moved")),
			patch("lms.lms.utils.get_lms_path", side_effect=AttributeError("moved")),
		):
			self.assertEqual(utils.lms_route("courses/x"), "/lms/courses/x")
			self.assertEqual(utils.lms_route(), "/lms")
			self.assertEqual(lms_page.learner_config()["lmsPath"], "lms")

	def test_assets_are_versioned_by_content(self):
		for path in ("css/learner.css", "js/learner.js"):
			self.assertTrue(os.path.isfile(frappe.get_app_path("activationlab", "public", *path.split("/"))))
			self.assertRegex(
				lms_page.asset_url(path), rf"^/assets/activationlab/{re.escape(path)}\?v=[0-9a-f]{{10}}$"
			)


class TestLearningTemplate(IntegrationTestCase):
	"""The same page through the Learning app's real template, when the bench has it.
	It is a build output; dev/setup-bench.sh makes a stand-in from the template's source."""

	def setUp(self):
		if not os.path.isfile(frappe.get_app_path("lms", "www", "_lms.html")):
			self.skipTest("The Learning app's page template is not built on this bench.")
		frappe.set_user("Administrator")
		self.transactions = emulated_transactions()
		self.transactions.__enter__()
		make_user(LEARNER, "Page learner")
		frappe.db.commit()

	def tearDown(self):
		self.transactions.__exit__(None, None, None)
		frappe.set_user("Administrator")
		frappe.local.request = None
		frappe.db.rollback()

	def test_learning_page_renders_with_the_tags(self):
		frappe.set_user(LEARNER)
		set_request(method="GET", path="/lms/courses")
		try:
			response = get_response()
		finally:
			frappe.set_user("Administrator")

		html = response.get_data(as_text=True)
		self.assertEqual(response.status_code, 200)
		self.assertIn('<div id="app">', html)
		self.assertEqual(html.count("data-activationlab"), 1)
		self.assertLess(html.index("learner.js"), html.index("</head>"))
