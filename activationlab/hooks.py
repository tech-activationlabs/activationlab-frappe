app_name = "activationlab"
app_title = "Activation Lab"
app_publisher = "Activation Lab"
app_description = "Course purchase through Stripe Checkout and learner pages for Frappe Learning"
app_email = "tech@activationlab.ai"
app_license = "mit"

required_apps = ["frappe/lms", "frappe/payments"]

# The fields Stripe Mode and Reversed In Stripe on LMS Payment. See install.py.
after_install = "activationlab.install.after_install"
after_migrate = "activationlab.install.after_migrate"

# The /lms pages: sign-in with a way back, the Stripe checkout for billing addresses, and the
# learner styles and script. See lms_page.py.
page_renderer = ["activationlab.lms_page.LMSPage"]

# The old purchase routes. The Learning app's billing form asks get_payment_link for an address, and
# a course now gets this app's checkout. The Payments app's card form charges a card token at an
# amount sent by the browser, which Stripe refuses, and it is open to guests, so it is closed.
# Remove the second line when the Payments app pays through PaymentIntents.
override_whitelisted_methods = {
	"lms.lms.payments.get_payment_link": "activationlab.checkout.get_payment_link",
	"payments.templates.pages.stripe_checkout.make_payment": "activationlab.checkout.refuse_card_token_payment",
}

# The public website's forms may read this site's reply. See website_forms.py.
before_request = ["activationlab.website_forms.allow_the_website"]

# A change of price or sale closes the course's open Stripe pages that offer something else.
doc_events = {
	"LMS Course": {
		"on_update": "activationlab.checkout.course_changed",
	},
}

# Stands in for a Stripe webhook: enrols learners who paid and never came back from Stripe, and
# takes back access after a refund or a dispute.
scheduler_events = {
	"hourly": ["activationlab.checkout.reconcile"],
}
