"""Course purchase through Stripe Checkout, with Stripe replaced by FakeStripe."""

import json
from unittest.mock import patch
from urllib.parse import unquote

import frappe
import stripe
from frappe.handler import execute_cmd
from frappe.tests import IntegrationTestCase
from frappe.utils import set_request

from activationlab import checkout, stripe_api
from activationlab.tests.utils import (
	TEST_KEY,
	FakeStripe,
	delete_course,
	delete_user,
	emulated_transactions,
	make_course,
	make_user,
	use_test_gateway,
)

LEARNER = "al-learner@example.com"
OTHER_LEARNER = "al-other@example.com"
RENAMED_LEARNER = "al-renamed@example.com"


class CheckoutTestCase(IntegrationTestCase):
	"""Shared helpers: call the addresses as a learner, and read what they left on the site."""

	def call(self, method, user, **kwargs):
		frappe.set_user(user)
		frappe.local.response = frappe._dict()
		try:
			return method(**kwargs)
		finally:
			frappe.set_user("Administrator")

	def buy(self, user=LEARNER, course=None):
		return self.call(checkout.buy, user, course=course or self.course)

	def complete(self, session_id, user=LEARNER):
		return self.call(checkout.complete, user, session_id=session_id)

	def payments(self, user=LEARNER, course=None):
		return frappe.get_all(
			"LMS Payment",
			filters={"member": user, "payment_for_document": course or self.course},
			fields=[
				"name",
				"amount",
				"currency",
				"order_id",
				"payment_id",
				"payment_received",
				"al_stripe_mode",
				"al_reversal",
			],
			order_by="creation asc",
		)

	def enrolments(self, user=LEARNER, course=None):
		return frappe.get_all(
			"LMS Enrollment",
			filters={"member": user, "course": course or self.course},
			fields=["name", "payment"],
		)

	def assertRedirect(self, response, location, status=303):
		self.assertIsNotNone(response, frappe.local.response)
		self.assertEqual(response.status_code, status)
		self.assertEqual(response.headers["Location"], location)

	def assertPage(self, status):
		self.assertEqual(frappe.local.response.get("type"), "page")
		self.assertEqual(frappe.local.response.get("http_status_code"), status)

	def bought_session(self, user=LEARNER):
		"""Start a purchase and return its Checkout Session id."""
		response = self.buy(user)
		self.assertEqual(response.status_code, 303)
		return response.headers["Location"].rsplit("/", 1)[-1]

	def paid_and_enrolled(self, user=LEARNER):
		"""Buy, pay and come back. Return the Checkout Session id."""
		session_id = self.bought_session(user)
		self.stripe.pay(session_id)
		self.complete(session_id, user)
		self.assertEqual(len(self.enrolments(user)), 1)
		return session_id


class TestCheckout(CheckoutTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.transactions = emulated_transactions()
		self.transactions.__enter__()
		use_test_gateway()
		make_user(LEARNER, "Learner")
		make_user(OTHER_LEARNER, "Other")
		self.course = make_course("Activation Lab test course")
		# The records exist before each request, as on a live site.
		frappe.db.commit()
		self.stripe = FakeStripe()
		self.stripe_patch = self.stripe.installed()
		self.stripe_patch.__enter__()

	def tearDown(self):
		self.stripe_patch.__exit__(None, None, None)
		self.transactions.__exit__(None, None, None)
		frappe.set_user("Administrator")
		frappe.local.response = frappe._dict()
		frappe.local.request = None
		frappe.local.form_dict = frappe._dict()
		frappe.db.rollback()

	# buy

	def test_guest_goes_to_sign_up_and_returns_to_the_purchase(self):
		response = self.buy(user="Guest")
		self.assertRedirect(response, f"/login?redirect-to=/lms/billing/course/{self.course}#signup")
		self.assertFalse(self.stripe.created)

	def test_session_carries_the_server_side_price_and_the_learner(self):
		response = self.buy()

		[payment] = self.payments()
		self.assertEqual((payment.amount, payment.currency, payment.payment_received), (299, "USD", 0))
		self.assertEqual(payment.al_stripe_mode, "test")
		[params] = self.stripe.created
		session_id = payment.order_id
		self.assertRedirect(response, f"https://checkout.stripe.com/c/pay/{session_id}")
		self.assertEqual(params["mode"], "payment")
		self.assertEqual(
			params["line_items"],
			[
				{
					"quantity": 1,
					"price_data": {
						"currency": "usd",
						"unit_amount": 29900,
						"product_data": {
							"name": "Activation Lab test course",
							"description": "A course for the activationlab tests.",
						},
					},
				}
			],
		)
		self.assertEqual(params["customer_email"], LEARNER)
		self.assertEqual(params["client_reference_id"], payment.name)
		expected_metadata = {
			"app": "activationlab",
			"site": frappe.local.site,
			"lms_payment": payment.name,
			"course": self.course,
			"member": LEARNER,
		}
		self.assertEqual(params["metadata"], expected_metadata)
		self.assertEqual(params["payment_intent_data"]["metadata"], expected_metadata)
		self.assertTrue(
			params["success_url"].endswith(
				"/api/method/activationlab.checkout.complete?session_id={CHECKOUT_SESSION_ID}"
			)
		)
		self.assertTrue(params["cancel_url"].endswith(f"/lms/courses/{self.course}"))
		self.assertTrue(params["idempotency_key"].startswith(f"activationlab-{payment.name}-new-"))

	def test_session_offers_cards_only(self):
		self.buy()
		self.assertEqual(self.stripe.created[0]["payment_method_types"], ["card"])
		self.assertNotIn("allowed_payment_method_types", self.stripe.created[0])

	def test_second_click_reuses_the_open_session(self):
		first = self.buy()
		second = self.buy()
		self.assertEqual(first.headers["Location"], second.headers["Location"])
		self.assertEqual(len(self.stripe.created), 1)
		self.assertEqual(len(self.payments()), 1)

	def test_expired_session_is_replaced_on_the_same_payment(self):
		first_id = self.bought_session()
		self.stripe.expire(first_id)
		second_id = self.bought_session()

		self.assertNotEqual(first_id, second_id)
		[payment] = self.payments()
		self.assertEqual(payment.order_id, second_id)
		keys = [params["idempotency_key"] for params in self.stripe.created]
		self.assertNotEqual(keys[0], keys[1])
		self.assertIn(f"-{first_id}-", keys[1])

	def test_price_change_starts_a_new_payment_and_closes_the_old_page(self):
		first_id = self.bought_session()
		frappe.db.set_value("LMS Course", self.course, "course_price", 349)
		self.bought_session()

		self.assertEqual([p.amount for p in self.payments()], [299, 349])
		self.assertEqual(self.stripe.created[1]["line_items"][0]["price_data"]["unit_amount"], 34900)
		self.assertEqual(self.stripe.expired, [first_id])
		self.assertEqual(self.stripe.sessions[first_id]["status"], "expired")

	def test_enrolled_learner_goes_to_the_course(self):
		self.paid_and_enrolled()

		response = self.buy()
		self.assertRedirect(response, f"/lms/courses/{self.course}")
		self.assertEqual(len(self.stripe.created), 1)

	def test_course_not_on_sale_goes_to_the_course(self):
		for change in ({"paid_course": 0}, {"published": 0}, {"disable_self_learning": 1}):
			frappe.db.set_value(
				"LMS Course", self.course, {"paid_course": 1, "published": 1, "disable_self_learning": 0}
			)
			frappe.db.set_value("LMS Course", self.course, change)
			response = self.buy()
			self.assertRedirect(response, f"/lms/courses/{self.course}")
		self.assertFalse(self.stripe.created)
		self.assertFalse(self.payments())

	def test_unknown_course_is_not_found(self):
		self.assertIsNone(self.buy(course="no-such-course"))
		self.assertPage(404)

	def test_stripe_failure_leaves_no_payment(self):
		with (
			patch("activationlab.stripe_api.create_session", side_effect=stripe.APIConnectionError("down")),
			patch("activationlab.checkout.log"),
		):
			self.assertIsNone(self.buy())
		self.assertPage(503)
		self.assertFalse(self.payments())

	def test_payment_still_processing_is_not_charged_again(self):
		session_id = self.bought_session()
		self.stripe.pay(session_id, payment_status="unpaid")

		self.assertIsNone(self.buy())
		self.assertPage(202)
		self.assertEqual(len(self.stripe.created), 1)

	def test_failed_later_payment_allows_a_new_checkout(self):
		session_id = self.bought_session()
		self.stripe.pay(session_id, payment_status="unpaid")
		self.stripe.fail(session_id)

		second_id = self.bought_session()

		self.assertNotEqual(second_id, session_id)
		[payment] = self.payments()
		self.assertEqual(payment.order_id, second_id)
		self.assertFalse(self.enrolments())

	def test_paid_but_not_returned_is_fulfilled_on_the_next_click(self):
		session_id = self.bought_session()
		self.stripe.pay(session_id)

		response = self.buy()
		self.assertRedirect(response, f"/lms/courses/{self.course}/learn/1-3")
		self.assertEqual(len(self.enrolments()), 1)
		self.assertEqual(len(self.stripe.created), 1)

	def test_buy_is_limited_per_learner_not_per_address(self):
		set_request(method="GET", path="/api/method/activationlab.checkout.buy")
		keys = [
			frappe.cache.make_key(f"activationlab:rate:buy:{user}:600") for user in (LEARNER, OTHER_LEARNER)
		]
		try:
			with patch("activationlab.checkout.BUY_LIMIT", 2):
				self.buy()
				self.buy()
				self.assertIsNone(self.buy())
				self.assertPage(429)
				# Another learner on the same network address is not held back.
				self.assertEqual(self.buy(OTHER_LEARNER).status_code, 303)
		finally:
			for key in keys:
				frappe.cache.delete(key)

	# complete

	def test_paid_return_enrols_once_and_opens_the_first_paid_lesson(self):
		session_id = self.bought_session()
		self.stripe.pay(session_id)

		response = self.complete(session_id)

		self.assertRedirect(response, f"/lms/courses/{self.course}/learn/1-3")
		[payment] = self.payments()
		self.assertEqual(payment.payment_received, 1)
		self.assertEqual(payment.payment_id, "pi_test_fake1")
		self.assertEqual(payment.order_id, session_id)
		self.assertEqual(payment.al_stripe_mode, "test")
		self.assertFalse(payment.al_reversal)
		self.assertEqual([e.payment for e in self.enrolments()], [payment.name])

	def test_replayed_return_changes_nothing(self):
		session_id = self.paid_and_enrolled()
		before = (self.payments(), self.enrolments())

		response = self.complete(session_id)

		self.assertRedirect(response, f"/lms/courses/{self.course}/learn/1-3")
		self.assertEqual((self.payments(), self.enrolments()), before)

	def test_enrolment_removed_by_staff_is_not_made_again(self):
		session_id = self.paid_and_enrolled()
		frappe.delete_doc("LMS Enrollment", self.enrolments()[0].name, ignore_permissions=True, force=True)

		self.complete(session_id)
		summary = checkout.reconcile()

		self.assertFalse(self.enrolments())
		self.assertEqual((summary.checked, summary.fulfilled), (1, []))

	def test_amount_mismatch_is_refused(self):
		for change in ({"amount_total": 100}, {"currency": "eur"}):
			session_id = self.bought_session()
			self.stripe.pay(session_id, **change)
			with patch("activationlab.checkout.log"):
				self.assertIsNone(self.complete(session_id))
			self.assertPage(403)
			self.assertFalse(self.enrolments())
			self.assertEqual(self.payments()[0].payment_received, 0)
			self.stripe.expire(session_id)

	def test_member_mismatch_is_refused(self):
		session_id = self.bought_session()
		self.stripe.pay(session_id)

		self.assertIsNone(self.complete(session_id, user=OTHER_LEARNER))
		self.assertPage(403)
		self.assertFalse(self.enrolments())
		self.assertFalse(self.enrolments(OTHER_LEARNER))
		self.assertEqual(self.payments()[0].payment_received, 0)

	def test_unpaid_session_is_refused(self):
		session_id = self.bought_session()
		self.assertIsNone(self.complete(session_id))
		self.assertPage(202)

		self.stripe.pay(session_id, payment_status="unpaid")
		self.assertIsNone(self.complete(session_id))
		self.assertPage(202)
		self.assertFalse(self.enrolments())

	def test_session_of_another_site_is_refused(self):
		session_id = self.bought_session()
		self.stripe.pay(session_id)
		self.stripe.sessions[session_id]["metadata"]["site"] = "another.site"

		with patch("activationlab.checkout.log"):
			self.assertIsNone(self.complete(session_id))
		self.assertPage(403)
		self.assertFalse(self.enrolments())

	def test_session_of_another_stripe_mode_is_refused(self):
		session_id = self.bought_session()
		self.stripe.pay(session_id, livemode=True)

		with patch("activationlab.checkout.log"):
			self.assertIsNone(self.complete(session_id))
		self.assertPage(403)
		self.assertFalse(self.enrolments())
		self.assertEqual(self.payments()[0].payment_received, 0)

	def test_guest_return_signs_in_first(self):
		response = self.complete("cs_test_fake9", user="Guest")
		location = response.headers["Location"]
		self.assertTrue(location.startswith("/login?redirect-to="))
		self.assertEqual(
			unquote(location.split("=", 1)[1].split("#")[0]),
			"/api/method/activationlab.checkout.complete?session_id=cs_test_fake9",
		)

	def test_unknown_or_malformed_session_is_not_found(self):
		self.assertIsNone(self.complete("cs_test_unknown"))
		self.assertPage(404)
		self.assertIsNone(self.complete("not a session id"))
		self.assertPage(404)

	def test_second_paid_checkout_is_recorded_and_reported(self):
		first_id, second_id = self.two_paid_checkouts()
		self.complete(first_id)
		frappe.cache.delete_value(f"activationlab:extra-payment:{second_id}")

		with patch("activationlab.checkout.log") as log:
			response = self.complete(second_id)

		self.assertRedirect(response, f"/lms/courses/{self.course}/learn/1-3")
		self.assertEqual([p.payment_received for p in self.payments()], [1, 1])
		self.assertEqual(len(self.enrolments()), 1)
		self.assertEqual(log.call_args.args[0], "Activation Lab: extra payment to refund")

	def test_renamed_learner_is_still_enrolled(self):
		session_id = self.bought_session()
		self.stripe.pay(session_id)
		frappe.rename_doc("User", LEARNER, RENAMED_LEARNER, force=True)

		response = self.complete(session_id, user=RENAMED_LEARNER)

		self.assertRedirect(response, f"/lms/courses/{self.course}/learn/1-3")
		self.assertEqual(len(self.enrolments(RENAMED_LEARNER)), 1)
		# The old name is still in Stripe's metadata, and refuses nobody.
		self.assertEqual(self.stripe.sessions[session_id]["metadata"]["member"], LEARNER)

	def test_renamed_course_is_still_enrolled(self):
		session_id = self.bought_session()
		self.stripe.pay(session_id)
		renamed = frappe.rename_doc("LMS Course", self.course, f"{self.course}-renamed", force=True)

		response = self.complete(session_id)

		self.assertRedirect(response, f"/lms/courses/{renamed}/learn/1-3")
		self.assertEqual(len(self.enrolments(course=renamed)), 1)

	# refunds and disputes

	def test_refund_takes_back_access_and_the_learner_cannot_enrol_again_by_hand(self):
		session_id = self.paid_and_enrolled()
		self.stripe.refund(session_id)

		summary = checkout.reconcile()

		self.assertEqual(summary.reversed, ["pi_test_fake1"])
		[payment] = self.payments()
		self.assertEqual((payment.payment_received, payment.al_reversal), (0, "Refunded"))
		self.assertEqual(payment.payment_id, "pi_test_fake1")
		self.assertFalse(self.enrolments())
		self.assertTrue(
			frappe.db.exists("Comment", {"reference_doctype": "LMS Payment", "reference_name": payment.name})
		)
		# The request a learner could send to /api/resource/LMS Enrollment, under their own permissions.
		frappe.set_user(LEARNER)
		try:
			with self.assertRaisesRegex(frappe.ValidationError, "complete the payment"):
				frappe.get_doc(
					{"doctype": "LMS Enrollment", "member": LEARNER, "course": self.course}
				).insert()
		finally:
			frappe.set_user("Administrator")

	def test_refunded_return_and_reconcile_do_not_enrol_again(self):
		session_id = self.paid_and_enrolled()
		self.stripe.refund(session_id)
		checkout.reconcile()

		self.assertIsNone(self.complete(session_id))
		self.assertPage(403)
		again = checkout.reconcile()

		self.assertEqual((again.fulfilled, again.reversed, again.failed), ([], [], []))
		self.assertFalse(self.enrolments())

	def test_refunded_learner_buys_again_on_a_new_payment(self):
		session_id = self.paid_and_enrolled()
		self.stripe.refund(session_id)
		checkout.reconcile()

		second_id = self.bought_session()

		self.assertNotEqual(second_id, session_id)
		self.assertEqual(len(self.payments()), 2)
		self.assertFalse(self.enrolments())

	def test_payment_cleared_by_staff_is_not_fulfilled_again(self):
		session_id = self.paid_and_enrolled()
		frappe.db.set_value("LMS Payment", self.payments()[0].name, "payment_received", 0)
		frappe.delete_doc("LMS Enrollment", self.enrolments()[0].name, ignore_permissions=True, force=True)

		response = self.buy()
		summary = checkout.reconcile()

		# The click opens a new checkout instead of reusing the old paid session.
		self.assertNotEqual(response.headers["Location"].rsplit("/", 1)[-1], session_id)
		self.assertEqual(summary.fulfilled, [])
		self.assertFalse(self.enrolments())

	def test_refund_before_the_return_enrols_nobody(self):
		session_id = self.bought_session()
		self.stripe.pay(session_id)
		self.stripe.refund(session_id)

		self.assertIsNone(self.complete(session_id))

		self.assertPage(403)
		[payment] = self.payments()
		self.assertEqual((payment.payment_received, payment.al_reversal), (0, "Refunded"))
		self.assertFalse(self.enrolments())
		self.assertEqual(checkout.reconcile().fulfilled, [])

	def test_refund_before_a_click_on_buy_starts_a_new_checkout(self):
		session_id = self.bought_session()
		self.stripe.pay(session_id)
		self.stripe.refund(session_id)

		second_id = self.bought_session()

		self.assertNotEqual(second_id, session_id)
		self.assertFalse(self.enrolments())
		self.assertEqual([p.al_reversal or None for p in self.payments()], ["Refunded", None])

	def test_partial_refund_and_dispute_take_back_access(self):
		session_id = self.paid_and_enrolled()
		self.stripe.refund(session_id, amount=5000)
		checkout.reconcile()
		self.assertEqual(self.payments()[0].al_reversal, "Refunded")
		self.assertFalse(self.enrolments())

		other_id = self.paid_and_enrolled(OTHER_LEARNER)
		self.stripe.dispute(other_id)
		checkout.reconcile()
		self.assertEqual(self.payments(OTHER_LEARNER)[0].al_reversal, "Disputed")
		self.assertFalse(self.enrolments(OTHER_LEARNER))

	def test_refund_of_one_of_two_payments_keeps_the_enrolment(self):
		first_id, second_id = self.two_paid_checkouts()
		with patch("activationlab.checkout.log"):
			self.complete(first_id)
			self.complete(second_id)
		_, second = self.payments()

		self.stripe.refund(first_id)
		checkout.reconcile()

		self.assertEqual([e.payment for e in self.enrolments()], [second.name])
		self.assertEqual([p.payment_received for p in self.payments()], [0, 1])

	def test_reversal_of_another_system_is_ignored(self):
		self.paid_and_enrolled()
		self.stripe.reversals.append("pi_from_another_system")
		self.stripe.payment_intents["pi_from_another_system"] = {"id": "pi_from_another_system"}

		summary = checkout.reconcile()

		self.assertEqual((summary.reversed, summary.failed), ([], []))
		self.assertEqual(len(self.enrolments()), 1)

	# reconcile

	def test_reconcile_fulfils_a_missed_payment_once(self):
		session_id = self.bought_session()
		self.stripe.pay(session_id)

		summary = checkout.reconcile()

		self.assertEqual(summary.fulfilled, [session_id])
		self.assertEqual(len(self.enrolments()), 1)
		self.assertEqual(self.payments()[0].payment_id, "pi_test_fake1")

		again = checkout.reconcile()
		self.assertEqual((again.checked, again.fulfilled, again.failed), (1, [], []))
		self.assertEqual(len(self.enrolments()), 1)

	def test_reconcile_skips_unpaid_and_foreign_sessions(self):
		unpaid_id = self.bought_session()
		self.stripe.pay(unpaid_id, payment_status="unpaid")
		foreign_id = self.bought_session(OTHER_LEARNER)
		self.stripe.pay(foreign_id)
		self.stripe.sessions[foreign_id]["metadata"] = {"order": "from another system"}

		summary = checkout.reconcile()

		self.assertEqual((summary.checked, summary.fulfilled, summary.failed), (0, [], []))
		self.assertFalse(self.enrolments())
		self.assertFalse(self.enrolments(OTHER_LEARNER))

	def test_reconcile_keeps_going_after_a_failure(self):
		bad_id = self.bought_session(OTHER_LEARNER)
		self.stripe.pay(bad_id, amount_total=1)
		good_id = self.bought_session()
		self.stripe.pay(good_id)

		with patch("activationlab.checkout.log"):
			summary = checkout.reconcile()

		self.assertEqual((summary.fulfilled, summary.failed), ([good_id], [bad_id]))
		self.assertEqual(len(self.enrolments()), 1)
		self.assertFalse(self.enrolments(OTHER_LEARNER))

	def test_reconcile_without_a_gateway_does_nothing(self):
		frappe.db.set_single_value("LMS Settings", "payment_gateway", None)
		self.stripe_patch.__exit__(None, None, None)
		try:
			with patch("activationlab.checkout.log"):
				self.assertEqual(checkout.reconcile().checked, 0)
		finally:
			self.stripe_patch = self.stripe.installed()
			self.stripe_patch.__enter__()

	# a change to the course

	def test_course_change_closes_open_pages_with_the_old_offer(self):
		open_id = self.bought_session()
		other_id = self.bought_session(OTHER_LEARNER)

		course = frappe.get_doc("LMS Course", self.course)
		course.short_introduction = "A new introduction."
		with patch("frappe.enqueue") as enqueue:
			course.save(ignore_permissions=True)
		self.assertEqual(self.expiry_jobs(enqueue), [])

		course.course_price = 199
		with patch("frappe.enqueue") as enqueue:
			course.save(ignore_permissions=True)
		self.assertEqual(self.expiry_jobs(enqueue), [{"course": self.course, "enqueue_after_commit": True}])

		self.assertEqual(sorted(checkout.expire_stale_sessions(self.course)), sorted([open_id, other_id]))
		self.assertEqual(checkout.expire_stale_sessions(self.course), [])

	def test_open_page_with_the_current_offer_stays_open(self):
		open_id = self.bought_session()
		self.assertEqual(checkout.expire_stale_sessions(self.course), [])
		self.assertEqual(self.stripe.sessions[open_id]["status"], "open")

		frappe.db.set_value("LMS Course", self.course, "published", 0)
		self.assertEqual(checkout.expire_stale_sessions(self.course), [open_id])

	# the old purchase routes

	def routed_call(self, user, cmd, **form):
		"""Call an API method as /api/method/CMD does, through the method overrides of hooks.py."""
		set_request(method="POST", path=f"/api/method/{cmd}")
		frappe.set_user(user)
		# set_user empties form_dict, so the form goes in afterwards.
		frappe.local.form_dict = frappe._dict(form)
		try:
			return execute_cmd(cmd)
		finally:
			frappe.set_user("Administrator")

	def test_old_card_form_is_closed_for_everyone(self):
		form = {
			"stripe_token_id": "tok_visa",
			"data": json.dumps({"amount": 0.5, "currency": "USD"}),
			"reference_doctype": "LMS Course",
			"reference_docname": self.course,
			"payment_gateway": "Stripe-Activation Lab Unit Tests",
		}
		with patch("stripe.Charge.create") as charge:
			for user in ("Guest", LEARNER):
				with self.assertRaisesRegex(frappe.PermissionError, "payment page is closed"):
					self.routed_call(user, "payments.templates.pages.stripe_checkout.make_payment", **form)
		charge.assert_not_called()
		self.assertFalse(frappe.get_all("Integration Request", filters={"reference_docname": self.course}))

	def test_learning_billing_form_gets_the_checkout_address(self):
		url = self.routed_call(
			LEARNER,
			"lms.lms.payments.get_payment_link",
			doctype="LMS Course",
			docname=self.course,
			address={"billing_name": "Learner", "country": "United States"},
			payment_for_certificate=0,
		)

		self.assertEqual(url, f"/api/method/activationlab.checkout.buy?course={self.course}")
		self.assertFalse(self.payments())

	def test_certificates_and_batches_keep_the_learning_flow(self):
		for doctype, certificate in (("LMS Course", 1), ("LMS Batch", 0)):
			with patch("lms.lms.payments.get_payment_link", return_value="learning") as learning:
				url = checkout.get_payment_link(doctype, self.course, {}, certificate)
			self.assertEqual(url, "learning")
			learning.assert_called_once()

	# the sign-up panel

	def test_offer_gives_the_public_facts_of_a_course_on_sale(self):
		offer = self.call(checkout.offer, "Guest", course=self.course)
		self.assertEqual(
			offer,
			{
				"name": self.course,
				"title": "Activation Lab test course",
				"short_introduction": "A course for the activationlab tests.",
				"amount": 299,
				"currency": "USD",
				"lessons": 3,
				"preview_lessons": 2,
			},
		)
		frappe.db.set_value("LMS Course", self.course, "published", 0)
		self.assertIsNone(self.call(checkout.offer, "Guest", course=self.course))
		self.assertIsNone(self.call(checkout.offer, "Guest", course="no-such-course"))

	# landing and amounts

	def test_landing_is_the_course_page_when_lessons_are_gated_or_all_open(self):
		frappe.db.set_value("LMS Course", self.course, "enforce_lesson_completion", 1)
		self.assertEqual(checkout.landing_url(self.course), f"/lms/courses/{self.course}")

		open_course = make_course("Activation Lab open course", lessons=2, previews=2)
		self.assertEqual(checkout.landing_url(open_course), f"/lms/courses/{open_course}")

	def test_landing_falls_back_to_the_course_page(self):
		with (
			patch("lms.lms.utils.get_ordered_lesson_rows", side_effect=AttributeError("changed")),
			patch("activationlab.checkout.log") as log,
		):
			self.assertEqual(checkout.landing_url(self.course), f"/lms/courses/{self.course}")
		log.assert_called_once()

	def test_minor_units(self):
		self.assertEqual(checkout.to_minor_units(299, "USD"), 29900)
		self.assertEqual(checkout.to_minor_units(19.99, "usd"), 1999)
		self.assertEqual(checkout.to_minor_units(0.285, "EUR"), 29)
		self.assertEqual(checkout.to_minor_units(1500, "JPY"), 1500)
		self.assertEqual(checkout.to_minor_units(1.234, "KWD"), 1230)

	def two_paid_checkouts(self):
		"""Two checkouts for one course, both paid. A price change made the second, and the stand-in
		for Stripe lets the first be paid although it was expired."""
		first_id = self.bought_session()
		frappe.db.set_value("LMS Course", self.course, "course_price", 349)
		second_id = self.bought_session()
		self.stripe.pay(first_id)
		self.stripe.pay(second_id)
		return first_id, second_id

	@staticmethod
	def expiry_jobs(enqueue):
		"""The arguments of the jobs that this app queued to expire Stripe pages."""
		return [
			call.kwargs
			for call in enqueue.call_args_list
			if call.args and call.args[0] == "activationlab.checkout.expire_stale_sessions"
		]


class TestOverlappingSteps(CheckoutTestCase):
	"""Two requests for one learner at the same time, on two database connections. Each request
	commits, as on the live site, so the records here are committed and removed at the end."""

	def setUp(self):
		frappe.set_user("Administrator")
		make_user(LEARNER, "Learner")
		self.course = make_course("Activation Lab overlap course")
		frappe.db.commit()
		# Frappe's secondary_connection() leaves its new connection in frappe.local.db the first time it
		# runs. Open it here, then go back to the first connection, so that each request has its own.
		with self.secondary_connection():
			frappe.db.rollback()
		frappe.local.db = self._primary_connection
		self.assertNotEqual(self.connection_id(), self.connection_id(secondary=True))
		self.stripe = FakeStripe()
		self.stripe_patch = self.stripe.installed()
		self.stripe_patch.__enter__()

	def tearDown(self):
		self.stripe_patch.__exit__(None, None, None)
		frappe.local.db = self._primary_connection
		frappe.set_user("Administrator")
		frappe.local.response = frappe._dict()
		with self.secondary_connection():
			frappe.db.rollback()
		frappe.db.rollback()
		delete_course(self.course)
		delete_user(LEARNER)
		frappe.db.commit()

	def connection_id(self, secondary=False):
		if not secondary:
			return frappe.db.sql("select connection_id()")[0][0]
		with self.secondary_connection():
			return frappe.db.sql("select connection_id()")[0][0]

	def start_reading(self):
		"""The second request has begun: MariaDB fixes what it reads at its first plain read. Plain SQL,
		because Frappe can answer a get_value from its cache without asking the database."""
		frappe.db.sql("select name from `tabLMS Course` where name = %s", self.course)

	def test_overlapping_clicks_share_one_payment_and_one_session(self):
		self.start_reading()
		with self.secondary_connection():
			first = self.buy()
		second = self.buy()

		self.assertEqual(first.headers["Location"], second.headers["Location"])
		self.assertEqual(len(self.stripe.created), 1)
		self.assertEqual(len(self.payments()), 1)

	def test_enrolment_made_while_a_click_waits_is_seen(self):
		with self.secondary_connection():
			session_id = self.bought_session()
		self.stripe.pay(session_id)
		self.start_reading()
		with self.secondary_connection():
			self.complete(session_id)

		response = self.buy()

		self.assertRedirect(response, f"/lms/courses/{self.course}")
		self.assertEqual(len(self.stripe.created), 1)
		self.assertEqual(len(self.payments()), 1)


class TestStripeApi(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.transactions = emulated_transactions()
		self.transactions.__enter__()
		use_test_gateway()
		frappe.db.commit()

	def tearDown(self):
		self.transactions.__exit__(None, None, None)
		frappe.db.rollback()

	def test_secret_key_comes_from_the_lms_payment_gateway(self):
		self.assertEqual(stripe_api.get_secret_key(), TEST_KEY)
		self.assertEqual(stripe_api.key_mode(), "test")

	def test_key_mode_follows_the_key_prefix(self):
		for key, mode in (
			("sk_live_x", "live"),
			("rk_live_x", "live"),
			("sk_test_x", "test"),
			("rk_test_x", "test"),
			("other", None),
		):
			with patch("activationlab.stripe_api.get_secret_key", return_value=key):
				self.assertEqual(stripe_api.key_mode(), mode)

	def test_payment_method_parameter_follows_the_api_version(self):
		self.assertEqual(stripe.api_version, "2024-06-20")
		self.assertEqual(stripe_api.payment_methods(["card"]), {"payment_method_types": ["card"]})
		with patch("stripe.api_version", "2026-09-30.endive"):
			self.assertEqual(stripe_api.payment_methods(["card"]), {"allowed_payment_method_types": ["card"]})

	def test_missing_gateway_is_reported(self):
		frappe.db.set_single_value("LMS Settings", "payment_gateway", None)
		with self.assertRaises(stripe_api.StripeNotConfigured):
			stripe_api.get_secret_key()

	def test_each_call_carries_the_key_and_retries(self):
		refund = stripe.Refund.construct_from({"id": "re_1", "payment_intent": "pi_1"}, TEST_KEY)
		dispute = stripe.Dispute.construct_from({"id": "dp_1", "payment_intent": "pi_1"}, TEST_KEY)
		with (
			patch("stripe.checkout.Session.create") as create,
			patch("stripe.checkout.Session.retrieve") as retrieve,
			patch("stripe.checkout.Session.expire") as expire,
			patch("stripe.checkout.Session.list") as listing,
			patch("stripe.PaymentIntent.retrieve") as payment_intent,
			patch("stripe.Refund.list") as refunds,
			patch("stripe.Dispute.list") as disputes,
		):
			refunds.return_value.auto_paging_iter.return_value = iter([refund])
			disputes.return_value.auto_paging_iter.return_value = iter([dispute])
			stripe_api.create_session("key-1", mode="payment")
			stripe_api.retrieve_session("cs_test_1")
			stripe_api.expire_session("cs_test_1")
			stripe_api.list_complete_sessions(1000)
			stripe_api.retrieve_payment_intent("pi_1")
			reversed_payments = stripe_api.list_reversed_payment_intents(2000)

		create.assert_called_once_with(
			api_key=TEST_KEY, idempotency_key="key-1", max_network_retries=2, mode="payment"
		)
		retrieve.assert_called_once_with("cs_test_1", api_key=TEST_KEY, max_network_retries=2)
		expire.assert_called_once_with("cs_test_1", api_key=TEST_KEY, max_network_retries=2)
		listing.assert_called_once_with(
			api_key=TEST_KEY, max_network_retries=2, created={"gte": 1000}, status="complete", limit=100
		)
		listing.return_value.auto_paging_iter.assert_called_once_with()
		payment_intent.assert_called_once_with(
			"pi_1", api_key=TEST_KEY, max_network_retries=2, expand=["latest_charge"]
		)
		for kind in (refunds, disputes):
			kind.assert_called_once_with(
				api_key=TEST_KEY, max_network_retries=2, created={"gte": 2000}, limit=100
			)
		self.assertEqual(reversed_payments, ["pi_1"])


class TestInstall(IntegrationTestCase):
	def test_lms_payment_has_the_fields_of_the_app(self):
		meta = frappe.get_meta("LMS Payment")
		self.assertEqual(meta.get_field("al_stripe_mode").options.split("\n"), ["", "test", "live"])
		self.assertEqual(meta.get_field("al_reversal").options.split("\n"), ["", "Refunded", "Disputed"])
		self.assertEqual(
			frappe.db.get_value("Custom Field", {"dt": "LMS Payment", "fieldname": "al_reversal"}, "module"),
			"Activation Lab",
		)
