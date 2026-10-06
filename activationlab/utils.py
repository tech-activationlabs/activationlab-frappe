"""Addresses shared by the checkout and the /lms pages."""

from urllib.parse import quote

import frappe

BUY_PATH = "/api/method/activationlab.checkout.buy"
COMPLETE_PATH = "/api/method/activationlab.checkout.complete"
STAFF_ROLES = {"Moderator", "Course Creator", "Batch Evaluator", "System Manager"}


def lms_path() -> str:
	"""The Learning app's path, lms unless the site config sets lms_path. The Learning app's own
	helper answers; if an update moves it, the site config and the default stand in."""
	try:
		from lms.lms.utils import get_lms_path

		return get_lms_path()
	except Exception:
		return (frappe.conf.get("lms_path") or "lms").strip("/")


def lms_route(path: str = "") -> str:
	"""An address inside the Learning app, such as /lms/courses/NAME. Path parts must be URL-encoded."""
	try:
		from lms.lms.utils import get_lms_route

		return get_lms_route(path)
	except Exception:
		return f"/{lms_path()}/{path}" if path else f"/{lms_path()}"


def course_page(course: str) -> str:
	return lms_route(f"courses/{quote(course, safe='')}")


def lesson_page(course: str, chapter: int, lesson: int) -> str:
	return lms_route(f"courses/{quote(course, safe='')}/learn/{chapter}-{lesson}")


def billing_page(course: str) -> str:
	return lms_route(f"billing/course/{quote(course, safe='')}")


def buy_url(course: str) -> str:
	return f"{BUY_PATH}?course={quote(course, safe='')}"


def login_url(target: str) -> str:
	"""The sign-in page on its sign-up view, returning to target, a URL-encoded path and query."""
	return f"/login?redirect-to={quote(target, safe='/')}#signup"


def is_staff(user: str | None = None) -> bool:
	user = user or frappe.session.user
	if user == "Guest":
		return False
	return bool(STAFF_ROLES & set(frappe.get_roles(user)))


def over_limit(action: str, limit: int, seconds: int) -> bool:
	"""Count one request for an action by the signed-in user. True when the count passes the limit
	within the window. The count is per user, not per network address, so that learners who share
	one address, as at a workshop or in an office, do not use up each other's requests. Guests and
	calls outside a web request, such as jobs and tests, are not counted."""
	user = frappe.session.user
	if user == "Guest" or not getattr(frappe.local, "request", None):
		return False
	key = frappe.cache.make_key(f"activationlab:rate:{action}:{user}:{seconds}")
	if not frappe.cache.get(key):
		frappe.cache.setex(key, seconds, 0)
	return frappe.cache.incrby(key, 1) > limit
