"""Every call this app makes to Stripe, in one place, so that tests can replace them.

The Stripe library is the one the Payments app installs (stripe~=10.12.0). The secret key goes with
each call and never into the library's global setting, which the Payments app also writes.
"""

import frappe
from frappe import _
from frappe.utils.password import get_decrypted_password

STRIPE_SETTINGS = "Stripe Settings"
NETWORK_RETRIES = 2


class StripeNotConfigured(frappe.ValidationError):
	pass


def get_secret_key() -> str:
	"""The secret key of the Stripe Settings record behind LMS Settings > Payment Gateway."""
	gateway = frappe.db.get_single_value("LMS Settings", "payment_gateway")
	if not gateway:
		raise StripeNotConfigured(_("No payment gateway is set in LMS Settings."))

	settings_doctype, controller = frappe.db.get_value(
		"Payment Gateway", gateway, ["gateway_settings", "gateway_controller"]
	) or (None, None)
	if settings_doctype != STRIPE_SETTINGS or not controller:
		raise StripeNotConfigured(_("The payment gateway {0} is not a Stripe gateway.").format(gateway))

	key = get_decrypted_password(STRIPE_SETTINGS, controller, "secret_key", raise_exception=False)
	if not key:
		raise StripeNotConfigured(_("The Stripe Settings {0} hold no secret key.").format(controller))
	return key


def key_mode() -> str | None:
	"""The mode of the secret key or restricted key, by its prefix: test, live, or None for another form."""
	key = get_secret_key()
	if key.startswith(("sk_live_", "rk_live_")):
		return "live"
	if key.startswith(("sk_test_", "rk_test_")):
		return "test"
	return None


def payment_methods(types: list[str]) -> dict:
	"""The Checkout Session parameter that limits the payment methods to types. Stripe's API version
	2026-09-30 replaced payment_method_types with allowed_payment_method_types. The Stripe library
	sends its own API version with each request, so the name follows the library: 2024-06-20 for the
	stripe~=10.12.0 that the Payments app installs."""
	import stripe

	if str(stripe.api_version or "") >= "2026-09-30":
		return {"allowed_payment_method_types": types}
	return {"payment_method_types": types}


def create_session(idempotency_key: str, **params):
	import stripe

	return stripe.checkout.Session.create(
		api_key=get_secret_key(),
		idempotency_key=idempotency_key,
		max_network_retries=NETWORK_RETRIES,
		**params,
	)


def retrieve_session(session_id: str):
	import stripe

	return stripe.checkout.Session.retrieve(
		session_id, api_key=get_secret_key(), max_network_retries=NETWORK_RETRIES
	)


def expire_session(session_id: str):
	"""Close an open Checkout Session, so that nobody can pay on it. Stripe refuses this for a session
	that is not open, with an InvalidRequestError."""
	import stripe

	return stripe.checkout.Session.expire(
		session_id, api_key=get_secret_key(), max_network_retries=NETWORK_RETRIES
	)


def list_complete_sessions(created_since: int):
	"""Every complete Checkout Session created at or after a Unix time, across all pages."""
	import stripe

	page = stripe.checkout.Session.list(
		api_key=get_secret_key(),
		max_network_retries=NETWORK_RETRIES,
		created={"gte": created_since},
		status="complete",
		limit=100,
	)
	return page.auto_paging_iter()


def retrieve_payment_intent(payment_intent_id: str):
	"""The PaymentIntent with its latest charge in full, which says whether the money was refunded or disputed."""
	import stripe

	return stripe.PaymentIntent.retrieve(
		payment_intent_id,
		api_key=get_secret_key(),
		max_network_retries=NETWORK_RETRIES,
		expand=["latest_charge"],
	)


def list_reversed_payment_intents(created_since: int) -> list[str]:
	"""The PaymentIntents of every refund and every dispute created at or after a Unix time, each once."""
	import stripe

	key = get_secret_key()
	found = []
	for kind in (stripe.Refund, stripe.Dispute):
		page = kind.list(
			api_key=key,
			max_network_retries=NETWORK_RETRIES,
			created={"gte": created_since},
			limit=100,
		)
		for item in page.auto_paging_iter():
			payment_intent = getattr(item, "payment_intent", None)
			if payment_intent and not isinstance(payment_intent, str):
				payment_intent = payment_intent.id
			if payment_intent and payment_intent not in found:
				found.append(payment_intent)
	return found
