"""Course purchase through Stripe Checkout.

buy        GET /api/method/activationlab.checkout.buy?course=NAME sends the learner to Stripe's page.
complete   GET /api/method/activationlab.checkout.complete?session_id=ID is where Stripe sends them back.
offer      GET /api/method/activationlab.checkout.offer?course=NAME gives the sign-up page a course's
           public facts: title, introduction, price and free lessons.
reconcile  runs every hour. It finishes any paid purchase whose learner never came back, and takes
           back the access of any payment that was refunded or disputed in Stripe.

A purchase is one LMS Payment row, written here with the course's own price, and one or more
Checkout Sessions that name that row in their metadata. fulfil() is the one place where a paid
session becomes a received payment and an enrolment, and reverse() the one place where a refund or
a dispute takes them back. Both are safe to call any number of times, from the return address, the
hourly job, or a webhook added later.

Steps for one learner run one at a time: each takes lock_learner() first. See its note.
"""

import hashlib
import json
import re
import sys
import time
from decimal import ROUND_HALF_UP, Decimal

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, flt, get_url, now_datetime
from werkzeug.utils import redirect

from activationlab import stripe_api
from activationlab.utils import (
	COMPLETE_PATH,
	billing_page,
	buy_url,
	course_page,
	lesson_page,
	lms_route,
	login_url,
	over_limit,
)

METADATA_APP = "activationlab"
LMS_SOURCE = "Website"
RECONCILE_HOURS = 48
REVERSAL_DAYS = 7
# Stripe holds an open Checkout Session for 24 hours unless told otherwise.
SESSION_HOURS = 24
SESSION_ID = re.compile(r"^cs_[A-Za-z0-9_]{1,250}$")
# Cards only, which Stripe confirms at once. Apple Pay and Google Pay come with cards. Methods that
# Stripe confirms days later, such as bank debits, need a webhook for the late answer first.
PAYMENT_METHOD_TYPES = ["card"]
# Requests per signed-in learner in ten minutes. Guests are only redirected, so they are not counted.
BUY_LIMIT = 30
COMPLETE_LIMIT = 60
LIMIT_SECONDS = 10 * 60
# Values of the custom field LMS Payment > Reversed In Stripe (install.py).
REFUNDED = "Refunded"
DISPUTED = "Disputed"
# Changes to a course after which its open Stripe pages no longer offer what it sells.
OFFER_FIELDS = ("course_price", "currency", "paid_course", "published", "disable_self_learning")

# Stripe counts these currencies in whole units, and these in thousandths.
ZERO_DECIMAL_CURRENCIES = frozenset(
	(
		"BIF",
		"CLP",
		"DJF",
		"GNF",
		"JPY",
		"KMF",
		"KRW",
		"MGA",
		"PYG",
		"RWF",
		"UGX",
		"VND",
		"VUV",
		"XAF",
		"XOF",
		"XPF",
	)
)
THREE_DECIMAL_CURRENCIES = frozenset(("BHD", "JOD", "KWD", "OMR", "TND"))

COURSE_FIELDS = [
	"name",
	"title",
	"short_introduction",
	"published",
	"paid_course",
	"disable_self_learning",
	"course_price",
	"currency",
]
PAYMENT_FIELDS = [
	"name",
	"member",
	"payment_for_document_type",
	"payment_for_document",
	"payment_for_certificate",
	"amount",
	"currency",
	"payment_received",
	"payment_id",
	"al_reversal",
]


class CheckoutRefused(frappe.ValidationError):
	"""A Checkout Session that must not lead to an enrolment. reason says why."""

	def __init__(self, reason: str, message: str):
		super().__init__(message)
		self.reason = reason


class PaymentProcessing(Exception):
	"""The learner paid with a method that Stripe confirms later, and has not confirmed yet."""


@frappe.whitelist(allow_guest=True, methods=["GET"])
def buy(course: str | None = None):
	"""Send the learner to Stripe to pay for a course, or to where they need to be first."""
	course = (course or "").strip()
	details = get_course(course) if course else None
	if not details:
		return message_page(_("Course not found"), _("There is no course at this address."), 404)

	user = frappe.session.user
	if user == "Guest":
		# After sign-in the learner lands on the course's billing address, which comes back here.
		return redirect(login_url(billing_page(details.name)), code=303)

	if over_limit("buy", BUY_LIMIT, LIMIT_SECONDS):
		return too_many_page(course_page(details.name))

	if is_enrolled(user, details.name) or not on_sale(details):
		return redirect(course_page(details.name), code=303)

	try:
		url = start_checkout(user, details)
	except PaymentProcessing:
		frappe.db.rollback()
		return processing_page()
	except Exception:
		frappe.db.rollback()
		log(
			"Activation Lab: checkout could not start",
			reference_doctype="LMS Course",
			reference_name=details.name,
		)
		return message_page(
			_("Payment is not available"),
			_("We could not open the payment page. Please try again in a few minutes."),
			503,
			action=course_page(details.name),
			label=_("Back to the course"),
		)

	frappe.db.commit()
	return redirect(url, code=303)


@frappe.whitelist(allow_guest=True, methods=["GET"])
def complete(session_id: str | None = None):
	"""Stripe's return address: confirm the payment with Stripe, enrol the learner, open the course."""
	session_id = (session_id or "").strip()
	if not SESSION_ID.match(session_id):
		return message_page(_("Payment not found"), _("This payment address is not valid."), 404)

	user = frappe.session.user
	if user == "Guest":
		return redirect(login_url(f"{COMPLETE_PATH}?session_id={session_id}"), code=303)

	if over_limit("complete", COMPLETE_LIMIT, LIMIT_SECONDS):
		return too_many_page()

	try:
		session = stripe_api.retrieve_session(session_id)
	except Exception as e:
		if is_missing_session(e):
			return message_page(_("Payment not found"), _("Stripe does not know this payment."), 404)
		log("Activation Lab: could not read a Checkout Session", session_id)
		return message_page(
			_("We could not check your payment"),
			_(
				"Stripe did not answer. Reload this page in a minute. If you paid, you will be enrolled within the hour."
			),
			503,
		)

	try:
		lock_learner(user)
		result = fulfil(session, member=user)
	except CheckoutRefused as e:
		frappe.db.rollback()
		return refused_page(session, e)
	except Exception:
		frappe.db.rollback()
		log("Activation Lab: paid Checkout Session not fulfilled", session_id)
		return message_page(
			_("Your payment went through"),
			_("We are finishing your enrolment. You will have access to the course within the hour."),
			202,
		)

	frappe.db.commit()
	if result.reversed:
		return reversed_page(result)
	return redirect(landing_url(result.course), code=303)


@frappe.whitelist(allow_guest=True, methods=["GET"])
def offer(course: str | None = None):
	"""The public facts of a course on sale, for the panel beside the sign-up form. None otherwise."""
	details = get_course((course or "").strip()) if course else None
	if not details or not on_sale(details):
		return None
	lessons = frappe.get_all("Course Lesson", filters={"course": details.name}, fields=["include_in_preview"])
	return {
		"name": details.name,
		"title": details.title,
		"short_introduction": details.short_introduction,
		"amount": flt(details.course_price),
		"currency": details.currency,
		"lessons": len(lessons),
		"preview_lessons": sum(1 for lesson in lessons if lesson.include_in_preview),
	}


@frappe.whitelist()
def get_payment_link(
	doctype: str,
	docname: str,
	address: dict | None = None,
	payment_for_certificate: int = 0,
	coupon_code: str | None = None,
	country: str | None = None,
):
	"""Stands in for lms.lms.payments.get_payment_link, through hooks.py. The Learning app's billing
	form calls it and opens the address it returns. A course goes to this app's Stripe Checkout.
	Certificates and batches keep the Learning app's own flow."""
	if doctype == "LMS Course" and not cint(payment_for_certificate):
		return buy_url(docname)

	from lms.lms.payments import get_payment_link as learning_payment_link

	return learning_payment_link(doctype, docname, address, payment_for_certificate, coupon_code, country)


@frappe.whitelist(allow_guest=True)
def refuse_card_token_payment(*args, **kwargs):
	"""Stands in for payments.templates.pages.stripe_checkout.make_payment, through hooks.py. That
	method charges a card token through Stripe's Charges API, which Stripe refuses, at an amount that
	the browser sends, and it is open to guests. Courses are sold through Stripe Checkout instead."""
	frappe.throw(
		_("This payment page is closed. Open the course and choose Buy to pay on Stripe's page."),
		frappe.PermissionError,
	)


def reconcile() -> dict:
	"""Stand in for a Stripe webhook. Runs every hour from the scheduler, as Administrator.

	First it fulfils every paid Checkout Session of this site from the last 48 hours that is not
	recorded yet, so a learner who paid and closed the tab before Stripe sent them back is enrolled.
	Then it reverses every recorded payment that was refunded or disputed in the last 7 days.
	"""
	summary = frappe._dict(checked=0, fulfilled=[], reversed=[], failed=[])
	now = int(time.time())
	try:
		sessions = list(stripe_api.list_complete_sessions(now - RECONCILE_HOURS * 3600))
	except stripe_api.StripeNotConfigured:
		return summary
	except Exception:
		log("Activation Lab: Checkout Sessions could not be listed")
		sessions = []

	for session in sessions:
		if not is_ours(session) or session.payment_status != "paid":
			continue
		summary.checked += 1
		if is_settled(session):
			continue
		try:
			lock_learner(payment_member(session) or metadata_of(session)["member"])
			result = fulfil(session)
			frappe.db.commit()
			if not result.reversed:
				summary.fulfilled.append(session.id)
		except Exception:
			frappe.db.rollback()
			log("Activation Lab: paid Checkout Session not fulfilled", session.id)
			summary.failed.append(session.id)

	try:
		payment_intents = stripe_api.list_reversed_payment_intents(now - REVERSAL_DAYS * 24 * 3600)
	except Exception:
		log("Activation Lab: refunds and disputes could not be listed")
		payment_intents = []

	for payment_intent in payment_intents:
		try:
			if reverse(payment_intent):
				frappe.db.commit()
				summary.reversed.append(payment_intent)
		except Exception:
			frappe.db.rollback()
			log("Activation Lab: refund or dispute not applied", payment_intent)
			summary.failed.append(payment_intent)
	return summary


def lock_learner(user: str):
	"""Hold the learner's User row until the transaction ends, so that purchase steps for one learner
	run one at a time: two clicks on Buy, two tabs, the return from Stripe and the hourly job.

	The open transaction is committed first, so call it before the step writes anything. MariaDB fixes
	what a transaction reads at its first plain read, and a locking read does not count as one. So
	every read after this call sees what another step committed while this one waited for the lock."""
	frappe.db.commit()
	frappe.db.sql("select name from `tabUser` where name = %s for update", user)


def start_checkout(user: str, course) -> str:
	"""Return the address of a Stripe page where the learner pays the course's current price."""
	lock_learner(user)
	if is_enrolled(user, course.name):
		# Another step enrolled the learner while this one waited.
		return course_page(course.name)

	amount, currency = flt(course.course_price), course.currency
	payment = get_pending_payment(user, course.name)

	if payment and payment.order_id:
		session = find_session(payment.order_id)
		if session and is_paid(session):
			# Paid earlier, but the learner never came back from Stripe.
			result = fulfil(session, member=user)
			if not result.reversed:
				return landing_url(course.name)
			# The money went back to the learner, and the row is closed. A new purchase starts below.
			payment = None
		elif session and session.status == "open":
			if same_price(payment, amount, currency) and session.url:
				return session.url
			# The price changed: nobody may pay the old one on this page any more.
			stripe_api.expire_session(session.id)
		elif session and session.status == "complete" and not payment_failed(session):
			# Paid by a method that Stripe confirms later. A second checkout now could take the money twice.
			raise PaymentProcessing

	if payment and not same_price(payment, amount, currency):
		# The old row keeps its own session; a new row carries the new price.
		payment = None
	if not payment:
		payment = new_payment(user, course, amount, currency)

	metadata = {
		"app": METADATA_APP,
		"site": frappe.local.site,
		"lms_payment": payment.name,
		"course": course.name,
		"member": user,
	}
	params = {
		"mode": "payment",
		**stripe_api.payment_methods(PAYMENT_METHOD_TYPES),
		"line_items": [
			{
				"quantity": 1,
				"price_data": {
					"currency": currency.lower(),
					"unit_amount": to_minor_units(amount, currency),
					"product_data": product_data(course),
				},
			}
		],
		"customer_email": frappe.db.get_value("User", user, "email") or user,
		"client_reference_id": payment.name,
		"metadata": metadata,
		"payment_intent_data": {"metadata": metadata, "description": course.title},
		# Stripe replaces {CHECKOUT_SESSION_ID} itself, so it stays as written.
		"success_url": get_url(COMPLETE_PATH) + "?session_id={CHECKOUT_SESSION_ID}",
		"cancel_url": get_url(course_page(course.name)),
	}
	# A repeated request with the same inputs gets the same session back from Stripe, and new
	# inputs get a new key, which Stripe requires.
	digest = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]
	idempotency_key = f"{METADATA_APP}-{payment.name}-{payment.order_id or 'new'}-{digest}"

	session = stripe_api.create_session(idempotency_key=idempotency_key, **params)
	frappe.db.set_value("LMS Payment", payment.name, "order_id", session.id)
	return session.url


def fulfil(session, member: str | None = None):
	"""Record a paid Checkout Session on its LMS Payment and enrol the learner. Safe to repeat.

	The session names its LMS Payment, and everything else comes from that row on this site: the
	learner, the course and the price. The names in the session's metadata are kept for people
	reading Stripe, and a rename of the learner or the course after checkout changes nothing here.
	member, when given, is the signed-in learner, who must be the one the payment row belongs to.
	Call lock_learner() for that learner first.

	Return a dict with course, payment, enrolled, and reversed, which is set when the money went back
	to the learner: then the row is closed and nobody is enrolled. Raise CheckoutRefused when the
	session must not lead to an enrolment.
	"""
	if not is_ours(session):
		raise CheckoutRefused("not_ours", _("This payment was not made on this site."))

	payment_name = metadata_of(session)["lms_payment"]
	owner = payment_member(session)
	if not owner or session.client_reference_id != payment_name:
		raise CheckoutRefused("mismatch", _("This payment does not match its record on this site."))
	if member and owner != member:
		raise CheckoutRefused(
			"wrong_member",
			_("This payment was made from another account. Sign in with the account you paid with."),
		)
	if not is_paid(session):
		raise CheckoutRefused("unpaid", _("Stripe has not confirmed this payment yet."))
	mode = stripe_api.key_mode()
	if mode and bool(session.livemode) != (mode == "live"):
		raise CheckoutRefused("mode", _("This payment was made in another Stripe mode."))

	# The rows stay locked until the transaction ends, so two calls for one learner queue.
	frappe.db.sql("select name from `tabUser` where name = %s for update", owner)
	payment = frappe.db.get_value("LMS Payment", payment_name, PAYMENT_FIELDS, as_dict=True, for_update=True)
	if (
		not payment
		or payment.member != owner
		or payment.payment_for_document_type != "LMS Course"
		or not payment.payment_for_document
		or payment.payment_for_certificate
	):
		raise CheckoutRefused("mismatch", _("This payment does not match its record on this site."))
	course = payment.payment_for_document
	paid_currency = (session.currency or "").upper()
	expected = to_minor_units(payment.amount, payment.currency)
	if paid_currency != (payment.currency or "").upper() or session.amount_total != expected:
		raise CheckoutRefused("amount", _("The amount paid does not match the price of the course."))

	payment_intent = get_id(session.payment_intent)
	if not payment_intent:
		raise CheckoutRefused("mismatch", _("Stripe gave no payment for this checkout."))
	result = frappe._dict(course=course, payment=payment.name, enrolled=False, reversed=None)

	if payment.al_reversal:
		# Refunded or disputed before. The row stays closed.
		result.reversed = payment.al_reversal
		return result
	if payment.payment_received:
		# Done before. The enrolment is not made again here.
		if payment.payment_id and payment.payment_id != payment_intent:
			report_extra_payment(
				session, payment.name, "Its LMS Payment was already paid by another payment."
			)
		return result

	other = frappe.db.exists("LMS Payment", {"payment_id": payment_intent, "name": ("!=", payment.name)})
	if other:
		raise CheckoutRefused("mismatch", _("This payment is already recorded on another record."))

	record = {
		"payment_id": payment_intent,
		"order_id": session.id,
		"al_stripe_mode": "live" if session.livemode else "test",
	}
	reason = money_returned(stripe_api.retrieve_payment_intent(payment_intent))
	if reason:
		# Refunded or disputed before the learner came back. Record it, closed, and enrol nobody.
		frappe.db.set_value(
			"LMS Payment", payment.name, {**record, "payment_received": 0, "al_reversal": reason}
		)
		result.reversed = reason
		return result

	already_enrolled = is_enrolled(owner, course)
	frappe.db.set_value("LMS Payment", payment.name, {**record, "payment_received": 1})
	if already_enrolled:
		# Two open checkouts for one course, both paid. The money is recorded; the second needs a refund.
		report_extra_payment(session, payment.name, "The learner was already enrolled in the course.")
		return result

	result.enrolled = enrol(owner, course, payment.name)
	return result


def reverse(payment_intent: str) -> bool:
	"""Take back what a refunded or disputed payment gave: the LMS Payment is no longer received, and
	the enrolment that it paid for is removed. Return True when this call made the change.

	Any refund, in full or in part, and any dispute count. Staff who want the learner to keep the
	course after a partial refund enrol them again by hand. An enrolment that another received
	payment covers moves to that payment and stays. An enrolment made by hand, with no payment, stays.
	"""
	row = frappe.db.get_value(
		"LMS Payment", {"payment_id": payment_intent}, ["name", "member", "al_reversal"], as_dict=True
	)
	if not row or row.al_reversal:
		return False
	reason = money_returned(stripe_api.retrieve_payment_intent(payment_intent))
	if not reason:
		return False

	lock_learner(row.member)
	payment = frappe.db.get_value("LMS Payment", row.name, PAYMENT_FIELDS, as_dict=True, for_update=True)
	if not payment or payment.al_reversal or payment.payment_id != payment_intent:
		return False
	frappe.db.set_value("LMS Payment", payment.name, {"payment_received": 0, "al_reversal": reason})

	course = payment.payment_for_document
	cover = frappe.db.get_value(
		"LMS Payment",
		{
			"member": payment.member,
			"payment_for_document_type": payment.payment_for_document_type,
			"payment_for_document": course,
			"payment_for_certificate": payment.payment_for_certificate,
			"payment_received": 1,
			"name": ("!=", payment.name),
		},
		"name",
	)
	removed = []
	for enrolment in frappe.get_all(
		"LMS Enrollment",
		filters={"member": payment.member, "course": course, "payment": payment.name},
		pluck="name",
	):
		if cover:
			frappe.db.set_value("LMS Enrollment", enrolment, "payment", cover)
		else:
			frappe.delete_doc("LMS Enrollment", enrolment, ignore_permissions=True)
			removed.append(enrolment)

	if removed:
		note = _("{0} in Stripe. Access to the course was removed: enrolment {1}.").format(
			reason, ", ".join(removed)
		)
	elif cover:
		note = _("{0} in Stripe. The enrolment stays, paid by {1}.").format(reason, cover)
	else:
		note = _("{0} in Stripe. There was no enrolment to remove.").format(reason)
	add_note("LMS Payment", payment.name, note)
	return True


def money_returned(payment_intent) -> str | None:
	"""REFUNDED or DISPUTED when Stripe no longer holds all of a payment, otherwise None."""
	charge = getattr(payment_intent, "latest_charge", None)
	if not charge or isinstance(charge, str):
		return None
	if getattr(charge, "disputed", False):
		return DISPUTED
	if getattr(charge, "refunded", False) or (getattr(charge, "amount_refunded", 0) or 0) > 0:
		return REFUNDED
	return None


def payment_failed(session) -> bool:
	"""For a complete session that is not paid yet: True when Stripe gave up on the payment."""
	payment_intent = get_id(session.payment_intent)
	if not payment_intent:
		return True
	return stripe_api.retrieve_payment_intent(payment_intent).status in (
		"requires_payment_method",
		"canceled",
	)


def enrol(member: str, course: str, payment: str) -> bool:
	"""Enrol a learner in a course once. Return True when this call made the enrolment."""
	# The Learning app locks the course row for the same check, so enrolments in one course queue.
	frappe.db.get_value("LMS Course", course, "name", for_update=True)
	if is_enrolled(member, course):
		return False
	frappe.get_doc(
		{"doctype": "LMS Enrollment", "member": member, "course": course, "payment": payment}
	).insert(ignore_permissions=True)
	return True


def course_changed(doc, method=None):
	"""LMS Course on_update, through hooks.py: when the price or the sale of a course changes, close
	its open Stripe pages that offer something else, after the change is saved."""
	if not doc.get_doc_before_save() or not any(doc.has_value_changed(field) for field in OFFER_FIELDS):
		return
	frappe.enqueue(
		"activationlab.checkout.expire_stale_sessions",
		course=doc.name,
		enqueue_after_commit=True,
	)


def expire_stale_sessions(course: str) -> list[str]:
	"""Expire the open Checkout Sessions of a course whose price or currency is not the course's
	price now, or of a course that is no longer on sale, so that nobody pays for an old offer."""
	details = get_course(course)
	since = add_to_date(now_datetime(), hours=-(SESSION_HOURS + 1))
	expired = []
	for row in frappe.get_all(
		"LMS Payment",
		filters={
			"payment_for_document_type": "LMS Course",
			"payment_for_document": course,
			"payment_for_certificate": 0,
			"payment_received": 0,
			"payment_id": ("is", "not set"),
			"order_id": ("is", "set"),
			"creation": (">=", since),
		},
		fields=["name", "order_id", "amount", "currency"],
	):
		if details and on_sale(details) and same_price(row, flt(details.course_price), details.currency):
			continue
		if expire_open_session(row.order_id):
			expired.append(row.order_id)
	return expired


def expire_open_session(session_id: str) -> bool:
	"""Expire a session if it is still open. Return True when Stripe expired it."""
	import stripe

	try:
		stripe_api.expire_session(session_id)
		return True
	except stripe.InvalidRequestError:
		# Not open any more, or unknown to this Stripe account: nothing to close.
		return False
	except Exception:
		log("Activation Lab: Checkout Session could not be expired", session_id)
		return False


def landing_url(course: str) -> str:
	"""Where a learner goes after buying: the first lesson that was not open as a preview.
	The course page when that cannot be worked out, such as after a change in the Learning app."""
	try:
		if frappe.db.get_value("LMS Course", course, "enforce_lesson_completion"):
			# The lesson gate would send them back to their first unfinished lesson anyway.
			return course_page(course)

		from lms.lms.utils import get_ordered_lesson_rows

		rows = get_ordered_lesson_rows(course)
		previews = set(
			frappe.get_all(
				"Course Lesson",
				filters={"name": ("in", [row.name for row in rows] or [""]), "include_in_preview": 1},
				pluck="name",
			)
		)
		for row in rows:
			if row.name not in previews:
				return lesson_page(course, row.chapter_idx, row.lesson_idx)
	except Exception:
		log("Activation Lab: first paid lesson not found", course)
	return course_page(course)


def get_course(course: str):
	return frappe.db.get_value(
		"LMS Course",
		course,
		COURSE_FIELDS,
		as_dict=True,
	)


def on_sale(course) -> bool:
	"""A course is sold here when it is published, paid, priced, and open to self-enrolment."""
	return bool(
		course.published
		and course.paid_course
		and not course.disable_self_learning
		and flt(course.course_price) > 0
		and course.currency
	)


def is_enrolled(user: str, course: str) -> bool:
	return bool(frappe.db.exists("LMS Enrollment", {"member": user, "course": course}))


def get_pending_payment(user: str, course: str):
	"""The learner's newest open purchase of a course. A row with a payment id is closed: paid, or
	refunded or disputed. Call after lock_learner(), so that a row another step just made is seen."""
	rows = frappe.get_all(
		"LMS Payment",
		filters={
			"member": user,
			"payment_for_document_type": "LMS Course",
			"payment_for_document": course,
			"payment_received": 0,
			"payment_for_certificate": 0,
			"payment_id": ("is", "not set"),
		},
		fields=["name", "order_id", "amount", "currency"],
		order_by="creation desc",
		limit=1,
	)
	return rows[0] if rows else None


def new_payment(user: str, course, amount: float, currency: str):
	"""A pending LMS Payment with the course's price. Stripe collects the billing details itself, so
	the Address that the Learning app's own billing form fills stays empty unless the learner has one."""
	ensure_source()
	payment = frappe.get_doc(
		{
			"doctype": "LMS Payment",
			"member": user,
			"billing_name": frappe.db.get_value("User", user, "full_name") or user,
			"address": frappe.db.get_value("Address", {"email_id": user}, "name", order_by="creation desc"),
			"amount": amount,
			"currency": currency,
			"source": LMS_SOURCE,
			"payment_for_document_type": "LMS Course",
			"payment_for_document": course.name,
			"al_stripe_mode": stripe_api.key_mode(),
		}
	)
	payment.flags.ignore_mandatory = True
	payment.insert(ignore_permissions=True)
	return frappe._dict(name=payment.name, order_id=None, amount=amount, currency=currency)


def ensure_source():
	"""The Learning app makes the LMS Source "Website" when it is installed. Make it if it is gone."""
	if not frappe.db.exists("LMS Source", LMS_SOURCE):
		frappe.get_doc({"doctype": "LMS Source", "source": LMS_SOURCE}).insert(ignore_permissions=True)


def product_data(course) -> dict:
	data = {"name": course.title or course.name}
	if course.short_introduction:
		data["description"] = course.short_introduction[:500]
	return data


def same_price(payment, amount: float, currency: str) -> bool:
	return payment.currency == currency and to_minor_units(payment.amount, currency) == to_minor_units(
		amount, currency
	)


def to_minor_units(amount, currency: str) -> int:
	"""An amount in the smallest unit Stripe counts, such as cents: 299 USD is 29900."""
	currency = (currency or "").upper()
	places = 0 if currency in ZERO_DECIMAL_CURRENCIES else 2
	value = (Decimal(str(flt(amount))) * (10**places)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
	if currency in THREE_DECIMAL_CURRENCIES:
		# Stripe counts these in thousandths but takes only whole tens of them.
		value *= 10
	return int(value)


def find_session(session_id: str):
	"""The Checkout Session, or None when Stripe does not know it, as after a change of Stripe account."""
	try:
		return stripe_api.retrieve_session(session_id)
	except Exception as e:
		if is_missing_session(e):
			return None
		raise


def is_missing_session(error: Exception) -> bool:
	import stripe

	return isinstance(error, stripe.InvalidRequestError) and getattr(error, "http_status", None) == 404


def metadata_of(session) -> dict:
	"""The session's metadata as a plain dict. Newer Stripe libraries drop dict methods from their objects."""
	metadata = session.metadata or {}
	return {
		key: metadata[key] for key in ("app", "site", "lms_payment", "course", "member") if key in metadata
	}


def is_ours(session) -> bool:
	"""A session this app made on this site. Other systems may share the Stripe account."""
	metadata = metadata_of(session)
	return (
		metadata.get("app") == METADATA_APP
		and metadata.get("site") == frappe.local.site
		and bool(metadata.get("lms_payment") and metadata.get("course") and metadata.get("member"))
	)


def payment_member(session) -> str | None:
	"""The learner of the LMS Payment that the session names, as the row says now."""
	return frappe.db.get_value("LMS Payment", metadata_of(session)["lms_payment"], "member")


def is_paid(session) -> bool:
	return session.mode == "payment" and session.status == "complete" and session.payment_status == "paid"


def is_settled(session) -> bool:
	"""A cheap check, without locks, for sessions recorded before: received, refunded or disputed."""
	payment = frappe.db.get_value("LMS Payment", metadata_of(session)["lms_payment"], "payment_id")
	return bool(payment and payment == get_id(session.payment_intent))


def get_id(value) -> str | None:
	"""A Stripe id, whether Stripe sent the id or the expanded object."""
	if not value:
		return None
	return value if isinstance(value, str) else value.id


def report_extra_payment(session, payment: str, reason: str):
	"""Log a payment that gives the learner nothing new, once, so that someone refunds it in Stripe."""
	key = f"activationlab:extra-payment:{session.id}"
	if frappe.cache.get_value(key):
		return
	frappe.cache.set_value(key, 1, expires_in_sec=7 * 24 * 3600)
	log(
		"Activation Lab: extra payment to refund",
		f"Checkout Session {session.id}, PaymentIntent {get_id(session.payment_intent)}, "
		f"LMS Payment {payment}, {session.amount_total} {session.currency}. {reason}",
		reference_doctype="LMS Payment",
		reference_name=payment,
	)


def add_note(doctype: str, name: str, text: str):
	"""A line in the record's timeline, so that staff see what the app did and when."""
	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Info",
			"reference_doctype": doctype,
			"reference_name": name,
			"content": text,
		}
	).insert(ignore_permissions=True)


def processing_page():
	return message_page(
		_("Your payment is being processed"),
		_(
			"Stripe has not confirmed this payment yet. If you paid, you will be enrolled within the hour after it does."
		),
		202,
	)


def reversed_page(result):
	if result.reversed == DISPUTED:
		message = _("This payment is disputed with the card issuer, so it gives no access to the course.")
	else:
		message = _("This payment was refunded, so it gives no access to the course.")
	return message_page(
		_("We could not confirm this payment"),
		message,
		403,
		action=course_page(result.course),
		label=_("Back to the course"),
	)


def refused_page(session, error: CheckoutRefused):
	if error.reason == "unpaid":
		return processing_page()
	if error.reason != "wrong_member":
		log(
			"Activation Lab: Checkout Session refused",
			f"Checkout Session {session.id}: {error.reason}. {error}",
		)
	return message_page(_("We could not confirm this payment"), str(error), 403)


def too_many_page(action: str | None = None):
	return message_page(
		_("Too many attempts"),
		_("Please wait a few minutes and try again."),
		429,
		action=action,
		label=_("Back to the course") if action else None,
	)


def log(title: str, details: str | None = None, **references):
	"""Write an Error Log outside the request's transaction, which a GET request rolls back.
	The log holds the details and the traceback of the exception being handled, if any."""
	traceback = frappe.get_traceback() if sys.exc_info()[0] else None
	message = "\n\n".join(part for part in (details, traceback) if part) or title
	frappe.log_error(title=title, message=message, defer_insert=True, **references)


def message_page(title: str, message: str, status: int, action: str | None = None, label: str | None = None):
	"""Answer a browser with a page in the site's own design. The API sends it when the method returns None."""
	frappe.respond_as_web_page(
		title,
		message,
		http_status_code=status,
		indicator_color="green" if status < 300 else "red",
		primary_action=action or lms_route("courses"),
		primary_label=label or _("Go to my courses"),
	)
