"""Two fields that the app adds to LMS Payment, made on install and checked on every migrate.

Stripe Mode         test or live: the Stripe mode of the payment, so that test purchases can be told
                    apart from real revenue, and cleared before go-live.
Reversed In Stripe  Refunded or Disputed: the payment no longer counts, and its enrolment was removed.

The fields carry the module "Activation Lab", so uninstalling the app removes them.
"""

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

MODULE = "Activation Lab"

CUSTOM_FIELDS = {
	"LMS Payment": [
		{
			"fieldname": "al_stripe_mode",
			"label": "Stripe Mode",
			"fieldtype": "Select",
			"options": "\ntest\nlive",
			"insert_after": "payment_id",
			"read_only": 1,
			"in_standard_filter": 1,
			"module": MODULE,
			"description": "Set by the activationlab app: the Stripe mode in which the payment was made.",
		},
		{
			"fieldname": "al_reversal",
			"label": "Reversed In Stripe",
			"fieldtype": "Select",
			"options": "\nRefunded\nDisputed",
			"insert_after": "al_stripe_mode",
			"read_only": 1,
			"in_standard_filter": 1,
			"module": MODULE,
			"description": "Set by the activationlab app when the payment was refunded or disputed in Stripe. "
			"The payment then no longer counts as received.",
		},
	]
}


def make_fields():
	create_custom_fields(CUSTOM_FIELDS, update=True)


def after_install():
	make_fields()


def after_migrate():
	make_fields()
