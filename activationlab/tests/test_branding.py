"""Activation Lab's icons where the Learning app has its own: the app manifest and the iPhone icons."""

from frappe.tests import IntegrationTestCase

from activationlab.branding import brand_icons, get_pwa_manifest

PAGE = (
	'<head><link rel="apple-touch-icon" href="/assets/lms/frontend/learning.png">'
	'<link rel="apple-touch-startup-image" href="/assets/lms/frontend/splash-1.png" media="x">'
	"</head>"
)


class TestBranding(IntegrationTestCase):
	def test_iphone_icon_is_ours_and_launch_screens_are_gone(self):
		html = brand_icons(PAGE)
		self.assertIn("/assets/activationlab/images/", html)
		self.assertNotIn("learning.png", html)
		self.assertNotIn("apple-touch-startup-image", html)

	def test_manifest_carries_our_icons(self):
		manifest = get_pwa_manifest()
		data = manifest.get_json() if hasattr(manifest, "get_json") else manifest
		icons = [icon["src"] for icon in data["icons"]]
		self.assertTrue(icons)
		self.assertTrue(all(src.startswith("/assets/activationlab/images/") for src in icons))
