# Activation Lab

A Frappe app for the Activation Lab course platform. It runs beside Frappe Learning (LMS) and Payments on Frappe 16 and does three things:

1. **Course purchase through Stripe Checkout.** The learner pays on Stripe's own page. The app takes the price from the course on the server, records an LMS Payment, checks the paid session with Stripe, and enrols the learner. It replaces the Payments app's card form, which Stripe refuses since card tokens on the Charges API reached end of life.
2. **Sign-in with a way back.** A signed-out visitor to any `/lms` address goes to the sign-up page and returns to that address afterwards. A link to a course, badge or profile that someone shares still shows its title, description and image in the preview.
3. **Learner pages.** A stylesheet and a script go into every `/lms` page: a reading column for lessons, the website's fonts, and fewer distractions for learners.

Nothing in the app names a course. Every published paid course on the site is sold the same way.

## How a purchase works

| Step | Address | What happens |
|---|---|---|
| 1 | `/lms/courses/NAME`, "Buy this course" | The learner script sends the click to step 2. A full page load of `/lms/billing/course/NAME`, such as the link in a payment reminder email, goes there too |
| 2 | `/api/method/activationlab.checkout.buy?course=NAME` | A guest goes to `/login?redirect-to=/lms/billing/course/NAME#signup` and comes back here after sign-in. An enrolled learner goes to the course. Otherwise the app writes a pending LMS Payment with the course's price and currency, creates a Checkout Session for cards and redirects to Stripe |
| 3 | Stripe's page | The learner pays by card, Apple Pay or Google Pay. The email field is filled in with the learner's address |
| 4 | `/api/method/activationlab.checkout.complete?session_id=ID` | The app reads the session from Stripe and checks: paid, the Stripe mode, the amount and currency of the LMS Payment, the payment record and the signed-in learner, and that the money was not refunded or disputed. Then it marks the LMS Payment received, stores the PaymentIntent id, enrols the learner, and opens the first lesson that was not a free preview |
| 5 | Every hour | `activationlab.checkout.reconcile` finishes any paid session of the last 48 hours that step 4 did not, for a learner who closed the tab before Stripe sent them back. It also applies every refund and dispute of the last 7 days (see below) |

Every step is safe to repeat. Steps for one learner run one at a time: two clicks, two tabs, the return from Stripe and the hourly job each wait for the learner's lock and then read what the others committed. A second click reuses the open session. A reload of step 4, or the hourly job, finds the payment recorded and changes nothing. Two checkouts for one course that are both paid are recorded, and the second is written to the Error Log as "Activation Lab: extra payment to refund".

The learner, the course and the price come from the LMS Payment on the site, which the session names. The names in the session's metadata are for people reading Stripe, so renaming a user or a course after checkout changes nothing.

`checkout.fulfil(session)` is the one function that turns a paid session into an enrolment, and `checkout.reverse(payment_intent)` the one that takes it back. A Stripe webhook can be added later by verifying the event with `stripe.Webhook.construct_event` and passing `checkout.session.completed` to `fulfil` and `charge.refunded` and `charge.dispute.created` to `reverse`.

Cancel on Stripe's page returns the learner to the course page. The pending LMS Payment stays, as the Learning app's own flow leaves it.

When a course's price, currency, paid flag, published flag or self-learning flag changes, a background job expires the course's open Stripe pages that offer something else, so nobody pays an old price. A learner who clicks Buy again gets a new page at the new price.

## Refunds and disputes

Refund in the Stripe Dashboard. Nothing else is needed on the site.

Within the hour the job finds the refund, and for that payment:

- the LMS Payment is no longer received, and its field Reversed In Stripe says Refunded or Disputed;
- the LMS Enrollment that the payment paid for is deleted, so the learner loses the course;
- a line in the LMS Payment's timeline says what was done.

Any refund counts, in full or in part, and so does any dispute, even one the school later wins. To let a learner keep the course after a partial refund, a staff member enrols them again by hand. An enrolment that another received payment covers moves to that payment and stays. An enrolment made by hand, with no payment, stays.

The Learning app lets a learner enrol themselves in a paid course when they have a received LMS Payment for it. A reversed payment is not received, so this is closed too. A refunded learner who wants the course again buys it again on a new payment.

## Settings it reads

| Setting | Where | Used for |
|---|---|---|
| Payment Gateway | LMS Settings | Names the Payment Gateway, such as `Stripe-Activation Lab`. Its controller is a Stripe Settings record |
| Secret Key | Stripe Settings, the record behind that gateway | Every call to Stripe. It is read on the server for each call and never reaches the browser. A test key (`sk_test_`) gives test mode, a live key gives live mode |
| Published, Paid Course, Course Price, Currency, Disable Self Learning | LMS Course | Which courses are sold, and for how much. A change closes the course's open Stripe pages |
| Include In Preview | Course Lesson | The first lesson without it is where a learner lands after buying |
| Enforce Lesson Completion | LMS Course | When it is on, the learner lands on the course page instead |
| Allow Guest Access | LMS Settings | When it is off, signed-out visitors to `/lms` go to sign-up |
| `lms_path` | Site config | The Learning app's address, `lms` unless set |

The app adds no doctype and no setting. It adds two read-only fields to LMS Payment, made on install and on every migrate (`install.py`), and removed when the app is uninstalled:

| Field | Values | Set when |
|---|---|---|
| Stripe Mode (`al_stripe_mode`) | `test`, `live` | The payment row is made, and again from Stripe's answer when it is paid. A paid session from the other mode is refused |
| Reversed In Stripe (`al_reversal`) | `Refunded`, `Disputed` | The payment was refunded or disputed |

The LMS Payment has no Address: Stripe collects the billing details. The Learning app's coupons, GST and USD conversion are not applied; the price is the course's price.

If the site config sets `block_endpoints`, the Learning app blocks API methods of other apps for learners and guests. Add `/api/method/activationlab.checkout.buy`, `/api/method/activationlab.checkout.complete` and `/api/method/activationlab.checkout.offer` to `allowed_custom_endpoints` in that case.

## The old purchase routes

`hooks.py` replaces two API methods of the other apps (`override_whitelisted_methods`):

| Method | Now |
|---|---|
| `lms.lms.payments.get_payment_link`, which the Learning app's billing form calls | For a course, returns this app's buy address. Certificates and batches keep the Learning app's own flow |
| `payments.templates.pages.stripe_checkout.make_payment`, the Payments app's card form | Refused for everyone. It charged a card token through Stripe's Charges API, which Stripe refuses, at an amount sent by the browser, and guests could call it |

Paid certificates and paid batches therefore cannot be bought on the site until they get a checkout of their own. Remove the second override when the Payments app pays through PaymentIntents.

## Payment methods

The session offers cards only, which Stripe confirms at once. Apple Pay and Google Pay come with cards. Methods that Stripe confirms days later, such as bank debits, need a webhook for the late answer first. Stripe's API version 2026-09-30 renamed the parameter from `payment_method_types` to `allowed_payment_method_types`. The Stripe library sends its own API version with each request, and the app picks the name to match: `stripe~=10.12.0` sends 2024-06-20.

## Limits

`buy` takes 30 and `complete` 60 requests in ten minutes from one signed-in learner, then shows a "Too many attempts" page. The count is per learner, so a room of learners on one network does not share it. Guests are only redirected, so they are not counted.

## The website's forms

The public website's contact, newsletter and grant forms send their entries to this site from another web address. A browser lets the website read the reply only when this site names the website's address in it. `website_forms.py` names it for two addresses only: `/api/method/ping`, which the website calls to check that it can read replies, and `/api/method/frappe.website.doctype.web_form.web_form.accept`, which takes the entry. Error replies carry the name too, so a visitor sees when an entry is refused. Every other address of the site stays closed to the website.

The website's addresses are in `WEBSITE_ORIGINS`. A site can add more in its site config, as the list `activationlab_website_origins`.

## The learner pages

A signed-out visitor, when LMS Settings > Allow Guest Access is off, gets a short page with status 200: the Learning app's title, description and image for the address, and a script that sends the browser on to sign-up and back. Link previews on LinkedIn or Slack read those tags without running the script. An unpublished course shows only the site's name. The page is never cached.

For a signed-in user, the app's page renderer serves the Learning app's own `/lms` template, which is a complete page, so Frappe adds no Website Settings head HTML and no Website Script to it. The renderer adds these to its `<head>`:

- `public/css/learner.css` and `public/js/learner.js`, with a version taken from each file's content.
- `window.activationlab`: the Learning app's path, the buy address, whether the user is staff, the published paid courses and the user's enrolled courses.
- The Google Fonts Instrument Serif and Inter.

What a learner sees:

- Lesson text at 17px in a column of about 46rem, in place of 14px across the whole width.
- Page headings in Instrument Serif, text in Inter, as on the website.
- No learning streak button, no "Complete your profile" card, no "Powered by Frappe Learning" mark.
- On the course page: no "Published" badge for anyone but staff; for an enrolled learner no price and no student count; with no reviews yet, no "0 course rating" line, and "Write a Review" stays.
- On a locked lesson of a paid course, "Start Learning" reads "Buy this course" and goes to the checkout.

The selectors come from Frappe Learning 2.63.0 (`frontend/src` at commit `87168fc`). After an LMS update, a rule that no longer matches leaves that part of the page as the Learning app draws it. Check the course page, a lesson and a locked lesson after each update.

## Deploy on Frappe Cloud

Frappe Cloud installs apps from GitHub only.

1. Run `frappe/apps/activationlab/dev/publish.sh`. It publishes this folder, as committed, to the public GitHub repository `tech-activationlabs/activationlab-frappe`, branch `main`, as one commit authored by Activation Lab, with none of the platform repository's history. It needs the deploy key behind the SSH host `github-activationlab`.
2. In the Frappe Cloud dashboard: Bench Group `activationlab`, Apps, Add App. For a public repository use the "Public Repository" tab and paste its URL. For a private one, install the Frappe Cloud GitHub App on the repository and use the "Private Repository" tab. Pick the branch.
3. Deploy the bench group when Frappe Cloud offers the update.
4. Site `activationlab.frappe.cloud`, Apps, Install App, Activation Lab. The install adds the two fields to LMS Payment.
5. Check that LMS Settings > Payment Gateway names the Stripe gateway and that its Stripe Settings hold the secret key.
6. Check that the Scheduler is on: the hourly job needs it.
7. If the site config sets `block_endpoints`, add the three addresses above to `allowed_custom_endpoints`.
8. After the install, put the new `frappe/account-pages/account.js` of the platform repository into Website Settings > Website Script, so the sign-up panel shows the course from the site.

## Go live

Before the Stripe key in Stripe Settings becomes a live key:

1. List the test purchases: LMS Payment with Stripe Mode `test`.
2. With the project team's yes, delete the enrolments those payments made and the payments themselves. Test learners keep no course.
3. Put the live key in Stripe Settings. From then on a test session is refused, and new rows say `live`.

`pyproject.toml` carries `[tool.bench.frappe-dependencies] frappe = ">=16.0.0,<17.0.0"`, which Frappe Cloud requires. `hooks.py` carries `required_apps = ["frappe/lms", "frappe/payments"]`; both are already on the bench group. The Stripe library comes with the Payments app (`stripe~=10.12.0`), so this app lists no Python dependency.

To remove the app, uninstall it from the site. The `/lms` pages and the Learning app's billing page then work as before; LMS Payments and enrolments made through the app stay.

## Test a purchase with Stripe test cards

With a test key in Stripe Settings:

1. Sign out, open `/lms/courses/NAME` of a paid course, and check that sign-up opens and returns to the course.
2. Sign in as a learner who is not enrolled. On the course page click "Buy this course". Stripe's test page opens with the course's name and price.
3. Pay with card `4242 4242 4242 4242`, any future expiry date, any CVC and any postal code.
4. The browser returns to the first paid lesson. In the desk, the LMS Payment shows Payment Received, a `pi_` Payment ID and a `cs_` Order ID, and an LMS Enrollment links to it.
5. Open the buy address again: it goes to the course, and Stripe shows no new session.
6. Card `4000 0000 0000 0002` is declined on Stripe's page, and nothing changes on the site.
7. For the hourly job: start a purchase, pay, and close the tab before Stripe redirects. Run `bench --site SITE execute activationlab.checkout.reconcile`, or wait for the hour. The learner is enrolled.
8. For a refund: refund the payment of step 3 in the Stripe Dashboard, then run the job or wait for the hour. The LMS Payment says Refunded, the enrolment is gone, and the course page offers the course again.

Stripe's Dashboard lists each session under Payments with the metadata `app`, `site`, `lms_payment`, `course` and `member`.

## Run the tests

The tests replace Stripe with an in-memory stand-in, so they need no Stripe key and make no call to Stripe.

```bash
dev/test.sh
```

The script starts MariaDB 10.11, Redis and the `frappe/bench` image with Docker Compose, builds a bench with Frappe, Payments and Learning at the commits that run on the platform, installs this app on the site `test.localhost`, and runs:

- `bench --site test.localhost run-tests --app activationlab`: the checkout, the reconcile job and the `/lms` pages.
- `node --test` on `tests/learner.test.cjs` and `tests/learner-css.test.cjs`: the learner script and stylesheet in jsdom, a simulated browser, against markup copied from the Learning app.

The first run takes some minutes. Later runs reuse the bench. Most tests undo their records. The two tests of overlapping requests (`TestOverlappingSteps`) need committed records, because each request runs on its own database connection, and remove them at the end. Remove it with `docker compose -f dev/docker-compose.yml down -v`.

On an existing bench: `bench --site SITE set-config allow_tests true`, then `bench --site SITE run-tests --app activationlab`.

## Files

| File | Holds |
|---|---|
| `activationlab/checkout.py` | The buy, return and offer addresses, `fulfil`, `reverse`, the hourly `reconcile`, and the stand-ins for the old purchase routes |
| `activationlab/stripe_api.py` | Every call to Stripe, and the lookup of the secret key |
| `activationlab/install.py` | The two fields on LMS Payment |
| `activationlab/lms_page.py` | The page renderer for `/lms` |
| `activationlab/utils.py` | Addresses shared by both |
| `activationlab/website_forms.py` | The website's address on the replies to its forms |
| `activationlab/public/` | The learner stylesheet and script |
| `activationlab/tests/` | The Python tests, the script and stylesheet tests (`*.test.cjs`) and their helpers |
| `dev/` | The local bench for the tests, and `publish.sh`, which publishes the app to GitHub |

## Licence

MIT. See `license.txt`.
