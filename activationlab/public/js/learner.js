// Activation Lab script for the Frappe Learning app at /lms.
// The page renderer of the activationlab app adds it to every /lms page, together with
// window.activationlab: { lmsPath, buyUrl, staff, paidCourses, enrolled }.
// It runs before the Learning app's own script, so its history hooks are in place before the
// app's router starts. It does three things:
// 1. Marks <html> with the page kind, the course, and whether the learner is enrolled, for learner.css.
// 2. Sends every move to a course's billing page to the Stripe checkout of the activationlab app.
// 3. On a locked lesson of a paid course, turns "Start Learning", which fails without a payment,
//    into "Buy this course", which goes to the same checkout.
(function () {
	"use strict";

	var config = window.activationlab;
	if (!config || window.__activationlabLearner) return;
	window.__activationlabLearner = true;

	var base = "/" + String(config.lmsPath || "lms").replace(/^\/+|\/+$/g, "");
	var paid = toSet(config.paidCourses);
	var enrolled = toSet(config.enrolled);
	var root = document.documentElement;
	var BUY_LABEL = "Buy this course";

	function toSet(list) {
		var set = {};
		(list || []).forEach(function (name) {
			set[name] = true;
		});
		return set;
	}

	function decode(part) {
		try {
			return decodeURIComponent(part);
		} catch (error) {
			return part;
		}
	}

	// The page kind of an address inside the app, and the course it belongs to.
	function describe(pathname) {
		if (pathname !== base && pathname.indexOf(base + "/") !== 0) return { page: "other" };
		var rest = pathname.slice(base.length).replace(/^\/+|\/+$/g, "");
		var parts = rest ? rest.split("/") : [];
		if (!parts.length) return { page: "home" };
		if (parts[0] === "billing" && parts[1] === "course" && parts[2] && parts.length === 3) {
			return { page: "billing", course: decode(parts[2]) };
		}
		if (parts[0] !== "courses") return { page: "other" };
		if (parts.length === 1) return { page: "courses" };
		if (parts[1] === "new" || parts[1] === "import") return { page: "other" };
		if (parts.length === 2) return { page: "course", course: decode(parts[1]) };
		if (parts[2] === "learn") return { page: "lesson", course: decode(parts[1]) };
		return { page: "other", course: decode(parts[1]) };
	}

	function buyUrl(course) {
		return config.buyUrl + "?course=" + encodeURIComponent(course);
	}

	// The course of a billing address, or null for any other address.
	function billingCourse(url) {
		var parsed;
		try {
			parsed = new URL(url, window.location.href);
		} catch (error) {
			return null;
		}
		if (parsed.origin !== window.location.origin) return null;
		var place = describe(parsed.pathname);
		return place.page === "billing" ? place.course : null;
	}

	function mark() {
		var place = describe(window.location.pathname);
		root.setAttribute("data-al-page", place.page);
		if (config.staff) root.setAttribute("data-al-staff", "1");
		if (place.course) {
			root.setAttribute("data-al-course", place.course);
			root.setAttribute("data-al-enrolled", enrolled[place.course] ? "1" : "0");
		} else {
			root.removeAttribute("data-al-course");
			root.removeAttribute("data-al-enrolled");
		}
	}

	// The course whose lesson is on screen, when it is paid and the learner has not bought it.
	function lockedPaidCourse() {
		var place = describe(window.location.pathname);
		if (place.page !== "lesson" || !paid[place.course] || enrolled[place.course]) return null;
		return place.course;
	}

	// The card that the Learning app shows on a lesson that is closed to the learner.
	function lockedCard(node) {
		var card = node && node.closest ? node.closest(".shadow.rounded-md.text-center") : null;
		return card && card.querySelector(".lucide-lock-keyhole") ? card : null;
	}

	function relabel() {
		if (!lockedPaidCourse()) return;
		var buttons = document.querySelectorAll(".shadow.rounded-md.text-center button");
		for (var i = 0; i < buttons.length; i++) {
			if (!lockedCard(buttons[i])) continue;
			// Change the text node in place, so that the app's own reference to it stays valid.
			var walker = document.createTreeWalker(buttons[i], NodeFilter.SHOW_TEXT, null);
			var node;
			while ((node = walker.nextNode())) {
				if (node.nodeValue.trim() && node.nodeValue.trim() !== BUY_LABEL) node.nodeValue = BUY_LABEL;
			}
		}
	}

	var pending = false;
	function refresh() {
		if (pending) return;
		pending = true;
		var run = function () {
			pending = false;
			mark();
			relabel();
		};
		if (window.requestAnimationFrame) window.requestAnimationFrame(run);
		else setTimeout(run, 0);
	}

	// The app's router moves between pages with history.pushState and replaceState.
	["pushState", "replaceState"].forEach(function (method) {
		var original = window.history[method];
		if (typeof original !== "function") return;
		window.history[method] = function (state, title, url) {
			var course = url == null ? null : billingCourse(String(url));
			if (course) {
				window.location.assign(buyUrl(course));
				return;
			}
			var result = original.apply(this, arguments);
			mark();
			refresh();
			return result;
		};
	});

	window.addEventListener("popstate", function () {
		var course = billingCourse(window.location.href);
		if (course) {
			window.location.assign(buyUrl(course));
			return;
		}
		mark();
		refresh();
	});

	// Clicks on a billing link or on the button of a locked paid lesson go to the checkout. This
	// listener runs before the app's own, so the app never sees these clicks.
	document.addEventListener(
		"click",
		function (event) {
			if (event.defaultPrevented || event.button !== 0) return;
			if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
			var target = event.target && event.target.closest ? event.target : null;
			if (!target) return;
			var link = target.closest("a[href]");
			var course = link ? billingCourse(link.getAttribute("href")) : null;
			if (!course && target.closest("button") && lockedCard(target)) course = lockedPaidCourse();
			if (!course) return;
			event.preventDefault();
			event.stopPropagation();
			window.location.assign(buyUrl(course));
		},
		true
	);

	mark();
	refresh();
	if (window.MutationObserver) {
		new MutationObserver(refresh).observe(root, { childList: true, subtree: true });
	}
})();
