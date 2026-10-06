"""Shared test helpers: records for a paid course, a stand-in for Stripe, and transactions that the
code under test can commit and roll back without ending the test's own transaction."""

import itertools
from contextlib import contextmanager
from unittest.mock import patch

import frappe
import stripe

TEST_KEY = "sk_test_ActivationLabUnitTests"
GATEWAY_NAME = "Activation Lab Unit Tests"


@contextmanager
def emulated_transactions():
	"""The code under test commits and rolls back like a request does. Here a commit moves a savepoint
	and a rollback returns to it, so the test still ends with one real rollback of everything."""
	db = frappe.db
	real_rollback = db.rollback
	db.savepoint("activationlab_test")

	def commit(*args, **kwargs):
		db.savepoint("activationlab_test")

	def rollback(*args, save_point=None, **kwargs):
		real_rollback(save_point=save_point or "activationlab_test")

	with patch.object(db, "commit", commit), patch.object(db, "rollback", rollback):
		yield


def make_user(email: str, first_name: str, roles=("LMS Student",)) -> str:
	if not frappe.db.exists("User", email):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": first_name,
				"user_type": "Website User",
				"send_welcome_email": 0,
				"roles": [{"role": role} for role in roles],
			}
		)
		user.insert(ignore_permissions=True)
	return email


def make_course(title: str, price: float = 299, lessons: int = 3, previews: int = 2, **values):
	"""A published paid course with one chapter. Its first `previews` lessons are open to everyone."""
	course = frappe.get_doc(
		{
			"doctype": "LMS Course",
			"title": title,
			"short_introduction": "A course for the activationlab tests.",
			"description": "A course for the activationlab tests.",
			"published": 1,
			"paid_course": 1,
			"course_price": price,
			"currency": "USD",
			"instructors": [{"instructor": "Administrator"}],
			**values,
		}
	).insert(ignore_permissions=True)

	chapter = frappe.get_doc(
		{"doctype": "Course Chapter", "course": course.name, "title": "Chapter one"}
	).insert(ignore_permissions=True)
	frappe.get_doc(
		{
			"doctype": "Chapter Reference",
			"chapter": chapter.name,
			"parent": course.name,
			"parenttype": "LMS Course",
			"parentfield": "chapters",
			"idx": 1,
		}
	).insert(ignore_permissions=True)

	for number in range(1, lessons + 1):
		lesson = frappe.get_doc(
			{
				"doctype": "Course Lesson",
				"course": course.name,
				"chapter": chapter.name,
				"title": f"Lesson {number}",
				"include_in_preview": 1 if number <= previews else 0,
			}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "Lesson Reference",
				"lesson": lesson.name,
				"parent": chapter.name,
				"parenttype": "Course Chapter",
				"parentfield": "lessons",
				"idx": number,
			}
		).insert(ignore_permissions=True)
	return course.name


def use_test_gateway():
	"""Stripe Settings with a test key behind LMS Settings > Payment Gateway. ignore_mandatory keeps
	the Payments app from checking the key with Stripe."""
	if not frappe.db.exists("Stripe Settings", GATEWAY_NAME):
		frappe.get_doc(
			{
				"doctype": "Stripe Settings",
				"gateway_name": GATEWAY_NAME,
				"publishable_key": "pk_test_ActivationLabUnitTests",
				"secret_key": TEST_KEY,
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)
	frappe.db.set_single_value("LMS Settings", "payment_gateway", f"Stripe-{GATEWAY_NAME}")


class FakeStripe:
	"""Checkout Sessions and PaymentIntents kept in memory, returned as real Stripe library objects."""

	def __init__(self, mode="test"):
		self.mode = mode
		self.sessions = {}
		self.payment_intents = {}
		self.created = []
		self.expired = []
		self.reversals = []
		self.ids = itertools.count(1)

	def create_session(self, idempotency_key, **params):
		number = next(self.ids)
		session_id = f"cs_test_fake{number}"
		item = params["line_items"][0]["price_data"]
		self.sessions[session_id] = {
			"id": session_id,
			"object": "checkout.session",
			"url": f"https://checkout.stripe.com/c/pay/{session_id}",
			"mode": params["mode"],
			"livemode": self.mode == "live",
			"status": "open",
			"payment_status": "unpaid",
			"amount_total": item["unit_amount"] * params["line_items"][0]["quantity"],
			"currency": item["currency"],
			"client_reference_id": params["client_reference_id"],
			"customer_email": params["customer_email"],
			"metadata": dict(params["metadata"]),
			"payment_intent": None,
		}
		self.created.append({"idempotency_key": idempotency_key, **params})
		return self.retrieve_session(session_id)

	def retrieve_session(self, session_id):
		if session_id not in self.sessions:
			raise stripe.InvalidRequestError("No such checkout.session", "id", http_status=404)
		return stripe.checkout.Session.construct_from(self.sessions[session_id], TEST_KEY)

	def expire_session(self, session_id):
		session = self.sessions.get(session_id)
		if not session:
			raise stripe.InvalidRequestError("No such checkout.session", "id", http_status=404)
		if session["status"] != "open":
			raise stripe.InvalidRequestError(
				"Only Checkout Sessions with a status in [open] can be expired.", None, http_status=400
			)
		self.expire(session_id)
		self.expired.append(session_id)
		return self.retrieve_session(session_id)

	def list_complete_sessions(self, created_since):
		return iter(
			[
				self.retrieve_session(sid)
				for sid, values in self.sessions.items()
				if values["status"] == "complete"
			]
		)

	def retrieve_payment_intent(self, payment_intent_id):
		if payment_intent_id not in self.payment_intents:
			raise stripe.InvalidRequestError("No such payment_intent", "id", http_status=404)
		return stripe.PaymentIntent.construct_from(self.payment_intents[payment_intent_id], TEST_KEY)

	def list_reversed_payment_intents(self, created_since):
		return list(dict.fromkeys(self.reversals))

	def key_mode(self):
		return self.mode

	# Changes that happen in Stripe

	def pay(self, session_id, **changes):
		"""The learner pays. payment_status="unpaid" stands for a method that Stripe confirms later."""
		number = session_id.rsplit("fake", 1)[-1]
		payment_intent = f"pi_test_fake{number}"
		self.sessions[session_id].update(
			{
				"status": "complete",
				"payment_status": "paid",
				"url": None,
				"payment_intent": payment_intent,
			}
		)
		self.sessions[session_id].update(changes)
		paid = self.sessions[session_id]["payment_status"] == "paid"
		self.payment_intents[payment_intent] = {
			"id": payment_intent,
			"object": "payment_intent",
			"status": "succeeded" if paid else "processing",
			"latest_charge": {
				"id": f"ch_test_fake{number}",
				"object": "charge",
				"amount": self.sessions[session_id]["amount_total"],
				"amount_refunded": 0,
				"refunded": False,
				"disputed": False,
			},
		}

	def fail(self, session_id):
		"""A payment that Stripe confirms later fails, and the PaymentIntent waits for a new method."""
		self.payment_intent_of(session_id)["status"] = "requires_payment_method"

	def refund(self, session_id, amount=None):
		"""Staff refund in the Stripe Dashboard: the rest of the payment, or a part of it. Stripe adds each
		refund to the charge's amount_refunded."""
		charge = self.payment_intent_of(session_id)["latest_charge"]
		charge["amount_refunded"] += amount or charge["amount"] - charge["amount_refunded"]
		charge["refunded"] = charge["amount_refunded"] >= charge["amount"]
		self.reversals.append(self.sessions[session_id]["payment_intent"])

	def dispute(self, session_id):
		"""The learner's card issuer opens a dispute."""
		self.payment_intent_of(session_id)["latest_charge"]["disputed"] = True
		self.reversals.append(self.sessions[session_id]["payment_intent"])

	def expire(self, session_id):
		self.sessions[session_id].update({"status": "expired", "url": None})

	def payment_intent_of(self, session_id):
		return self.payment_intents[self.sessions[session_id]["payment_intent"]]

	@contextmanager
	def installed(self):
		with (
			patch("activationlab.stripe_api.create_session", self.create_session),
			patch("activationlab.stripe_api.retrieve_session", self.retrieve_session),
			patch("activationlab.stripe_api.expire_session", self.expire_session),
			patch("activationlab.stripe_api.list_complete_sessions", self.list_complete_sessions),
			patch("activationlab.stripe_api.retrieve_payment_intent", self.retrieve_payment_intent),
			patch(
				"activationlab.stripe_api.list_reversed_payment_intents", self.list_reversed_payment_intents
			),
			patch("activationlab.stripe_api.key_mode", self.key_mode),
		):
			yield self


def delete_course(course: str):
	"""Remove a course made by make_course with its lessons, chapters, payments and enrolments. For
	tests whose records are committed. Rows go directly, because frappe.delete_doc leaves a "Deleted"
	comment behind for each document."""
	chapters = frappe.get_all("Course Chapter", filters={"course": course}, pluck="name")
	payments = frappe.get_all("LMS Payment", filters={"payment_for_document": course}, pluck="name")
	if payments:
		frappe.db.delete("Comment", {"reference_doctype": "LMS Payment", "reference_name": ("in", payments)})
	frappe.db.delete("LMS Enrollment", {"course": course})
	frappe.db.delete("LMS Payment", {"payment_for_document": course})
	if chapters:
		frappe.db.delete("Lesson Reference", {"parent": ("in", chapters)})
	frappe.db.delete("Course Lesson", {"course": course})
	frappe.db.delete("Course Chapter", {"course": course})
	for child in ("Chapter Reference", "Course Instructor"):
		frappe.db.delete(child, {"parent": course, "parenttype": "LMS Course"})
	frappe.db.delete("LMS Course", course)


def delete_user(email: str):
	"""Remove a user made by make_user, with the contact that Frappe makes for a website user. For
	tests whose records are committed."""
	contacts = frappe.get_all("Contact Email", filters={"email_id": email}, pluck="parent")
	if frappe.db.exists("User", email):
		frappe.delete_doc("User", email, force=True, ignore_permissions=True, delete_permanently=True)
		# Deleting a user deletes its Notification Settings, which Frappe keeps a copy and a note of.
		for doctype in ("User", "Notification Settings"):
			frappe.db.delete("Comment", {"comment_type": "Deleted", "subject": f"{doctype} {email}"})
			frappe.db.delete("Deleted Document", {"deleted_doctype": doctype, "deleted_name": email})
	for contact in contacts:
		for child in ("Contact Email", "Contact Phone", "Dynamic Link"):
			frappe.db.delete(child, {"parent": contact, "parenttype": "Contact"})
		frappe.db.delete("Contact", contact)
