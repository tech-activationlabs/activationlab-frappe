// Activation Lab script for the Frappe Learning app at /lms.
// The page renderer of the activationlab app adds it to every /lms page, together with
// window.activationlab: { lmsPath, buyUrl, staff, paidCourses, enrolled }.
// It runs before the Learning app's own script, so its history hooks are in place before the
// app's router starts. It does these things:
// 1. Marks <html> with the page kind, the course, enrolment and the lesson's place, for learner.css.
// 2. Sends every move to a course's billing page to the Stripe checkout of the activationlab app.
// 3. On a locked lesson of a paid course, turns "Start Learning", which fails without a payment,
//    into "Buy this course", which goes to the same checkout.
// 4. Reads the course outline from the Learning app and adds what the stylesheet cannot draw:
//    "Free preview" and "Opens when you buy" on chapters, locks on lessons that open after purchase,
//    "Try the free lessons" in the course card, a "Next lesson" card after the lesson text, the
//    learner's progress in the course card, and an action bar on phones.
// Each addition checks for the element it needs and does nothing when it is missing. Each node it
// adds carries data-al-added, and the node goes away when the element it belongs to is gone.
// It never removes or moves a node of the Learning app.
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
	var NUMBER = /^\d+-\d+$/;
	var CARD = ".min-w-80.max-w-sm";
	// The course outline of each course: undefined before the request, null while it runs, false after
	// a failure, and the outline after it arrives.
	var outlines = {};
	// The keys of the nodes that the current refresh keeps. Every other added node is removed.
	var kept = null;

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

	// The page kind of an address inside the app, the course it belongs to and the lesson's number.
	function describe(pathname) {
		if (pathname !== base && pathname.indexOf(base + "/") !== 0) return { page: "other" };
		var rest = pathname.slice(base.length).replace(/^\/+|\/+$/g, "");
		var parts = rest ? rest.split("/") : [];
		if (!parts.length) return { page: "home" };
		if (parts[0] === "billing" && parts[1] === "course" && parts[2] && parts.length === 3) {
			return { page: "billing", course: decode(parts[2]) };
		}
		if (parts[0] === "user" && parts[1] && parts.length === 2) return { page: "profile" };
		if (parts[0] === "you" && parts.length === 1) return { page: "you" };
		if (parts[0] !== "courses") return { page: "other" };
		if (parts.length === 1) return { page: "courses" };
		if (parts[1] === "new" || parts[1] === "import") return { page: "other" };
		if (parts.length === 2) return { page: "course", course: decode(parts[1]) };
		if (parts[2] === "learn") {
			var lesson = { page: "lesson", course: decode(parts[1]) };
			if (parts.length === 4 && NUMBER.test(parts[3])) lesson.number = parts[3];
			return lesson;
		}
		return { page: "other", course: decode(parts[1]) };
	}

	function buyUrl(course) {
		return config.buyUrl + "?course=" + encodeURIComponent(course);
	}

	function lessonUrl(course, number) {
		return base + "/courses/" + encodeURIComponent(course) + "/learn/" + number;
	}

	// The place of an address of this site, or null for another site or an address that cannot be read.
	function placeOf(url) {
		var parsed;
		try {
			parsed = new URL(url, window.location.href);
		} catch (error) {
			return null;
		}
		if (parsed.origin !== window.location.origin) return null;
		return describe(parsed.pathname);
	}

	// The course of a billing address, or null for any other address.
	function billingCourse(url) {
		var place = placeOf(url);
		return place && place.page === "billing" ? place.course : null;
	}

	// Writes an attribute only when it changes, and removes it for null.
	function setAttr(node, name, value) {
		if (value == null) {
			if (node.hasAttribute(name)) node.removeAttribute(name);
		} else if (node.getAttribute(name) !== value) {
			node.setAttribute(name, value);
		}
	}

	function setText(node, text) {
		if (node.textContent !== text) node.textContent = text;
	}

	// True when the learner sees the course as an offer: a paid course they have not bought, and not staff.
	function offered(course) {
		return !!course && !!paid[course] && !enrolled[course] && !config.staff;
	}

	function mark() {
		var place = describe(window.location.pathname);
		setAttr(root, "data-al-page", place.page);
		if (config.staff) setAttr(root, "data-al-staff", "1");
		setAttr(root, "data-al-courses", config.enrolled && config.enrolled.length ? "1" : "0");
		if (place.course) {
			setAttr(root, "data-al-course", place.course);
			setAttr(root, "data-al-enrolled", enrolled[place.course] ? "1" : "0");
			setAttr(root, "data-al-paid", paid[place.course] ? "1" : null);
		} else {
			setAttr(root, "data-al-course", null);
			setAttr(root, "data-al-enrolled", null);
			setAttr(root, "data-al-paid", null);
		}
		var label = lessonLabel(place);
		var value = label ? JSON.stringify(label) : "";
		if (root.style.getPropertyValue("--al-lesson-label") !== value) {
			if (value) root.style.setProperty("--al-lesson-label", value);
			else root.style.removeProperty("--al-lesson-label");
		}
		return place;
	}

	// "Chapter 2 · Lesson 1" from the address, with "Free preview · " for a free lesson of a course on offer.
	function lessonLabel(place) {
		if (place.page !== "lesson" || !place.number) return null;
		var parts = place.number.split("-");
		var label = "Chapter " + Number(parts[0]) + " · Lesson " + Number(parts[1]);
		var outline = offered(place.course) ? outlineOf(place.course) : null;
		var lesson = outline ? outline.byNumber[place.number] : null;
		return lesson && lesson.preview ? "Free preview · " + label : label;
	}

	// The course outline, from the Learning app's own read-only method. Null until it arrives, and
	// null for good after a failure. One request per course per page load.
	function outlineOf(course) {
		if (!course || !window.fetch) return null;
		if (Object.prototype.hasOwnProperty.call(outlines, course)) return outlines[course] || null;
		outlines[course] = null;
		var url =
			"/api/method/lms.lms.utils.get_course_outline?course=" +
			encodeURIComponent(course) +
			(enrolled[course] ? "&progress=1" : "");
		try {
			window
				.fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } })
				.then(function (response) {
					return response && response.ok ? response.json() : null;
				})
				.then(function (data) {
					outlines[course] = readOutline(data && data.message);
					refresh();
				})
				.catch(function () {
					outlines[course] = false;
				});
		} catch (error) {
			outlines[course] = false;
		}
		return null;
	}

	// The parts of the outline that the script uses, in outline order, or false when it is not an
	// outline. Lessons without a number of the form "2-1" are left out.
	function readOutline(chapters) {
		if (!Array.isArray(chapters) || !chapters.length) return false;
		var outline = { lessons: [], chapters: [], byNumber: {}, progress: false };
		for (var c = 0; c < chapters.length; c++) {
			var lessons = chapters[c] && Array.isArray(chapters[c].lessons) ? chapters[c].lessons : null;
			if (!lessons) return false;
			var chapter = { preview: 0, count: 0 };
			for (var l = 0; l < lessons.length; l++) {
				var item = lessons[l] || {};
				var number = String(item.number || "");
				if (!NUMBER.test(number)) continue;
				var lesson = {
					number: number,
					title: String(item.title || ""),
					chapter: c,
					preview: !!item.include_in_preview,
					complete: !!item.is_complete,
				};
				if (Object.prototype.hasOwnProperty.call(item, "is_complete")) outline.progress = true;
				outline.lessons.push(lesson);
				outline.byNumber[number] = lesson;
				chapter.count += 1;
				if (lesson.preview) chapter.preview += 1;
			}
			outline.chapters.push(chapter);
		}
		return outline.lessons.length ? outline : false;
	}

	// Keeps the one node marked with key next to anchor: after it ("after"), before it ("before"), or
	// inside it as a child ("inside"). Removes the node when anchor is null. update writes what differs.
	function place(key, anchor, where, build, update) {
		var node = document.querySelector('[data-al-added="' + key + '"]');
		if (!anchor || !anchor.parentNode) {
			if (node && node.parentNode) node.parentNode.removeChild(node);
			return null;
		}
		if (!node) {
			node = build();
			node.setAttribute("data-al-added", key);
		}
		if (where === "before") {
			if (anchor.previousSibling !== node) anchor.parentNode.insertBefore(node, anchor);
		} else if (where === "inside") {
			if (node.parentNode !== anchor) anchor.appendChild(node);
		} else if (anchor.nextSibling !== node) {
			anchor.parentNode.insertBefore(node, anchor.nextSibling);
		}
		if (update) update(node);
		if (kept) kept[key] = true;
		return node;
	}

	// Removes every added node that the last refresh did not keep.
	function sweep() {
		var nodes = document.querySelectorAll("[data-al-added]");
		for (var i = 0; i < nodes.length; i++) {
			if (!kept[nodes[i].getAttribute("data-al-added")] && nodes[i].parentNode) {
				nodes[i].parentNode.removeChild(nodes[i]);
			}
		}
	}

	function element(tag, className, text) {
		var node = document.createElement(tag);
		if (className) node.className = className;
		if (text) node.textContent = text;
		return node;
	}

	// The course page's lesson rows and the lesson page's outline, in the aside or the phone sheet.
	function outlineLinks() {
		var links = [];
		var groups = [document.querySelectorAll(".outline-lesson a[href]"), document.querySelectorAll("div.border-b + ul a[href]")];
		for (var g = 0; g < groups.length; g++) {
			for (var i = 0; i < groups[g].length; i++) {
				if (links.indexOf(groups[g][i]) < 0) links.push(groups[g][i]);
			}
		}
		return links;
	}

	// Locks on the lessons that open only after purchase, and a chip on each chapter of the course page.
	function chipsAndLocks(where, outline) {
		var gate = !!outline && offered(where.course);
		var links = outlineLinks();
		for (var i = 0; i < links.length; i++) {
			var target = placeOf(links[i].getAttribute("href"));
			var lesson =
				gate && target && target.page === "lesson" && target.course === where.course && target.number
					? outline.byNumber[target.number]
					: null;
			setAttr(links[i], "data-al-locked", lesson && !lesson.preview ? "1" : null);
		}
		var items = where.page === "course" ? document.querySelectorAll(".chapter-item") : [];
		for (var c = 0; c < items.length; c++) {
			var chapter = gate ? outline.chapters[c] : null;
			setAttr(items[c], "data-al-chip", chapter ? (chapter.preview ? "preview" : "locked") : null);
		}
	}

	// "Try the free lessons" after the buy button of each course card, when the course has free lessons.
	function freeLessons(where, outline) {
		if (where.page !== "course" || !outline || !offered(where.course)) return;
		var free = outline.lessons.filter(function (lesson) {
			return lesson.preview;
		});
		if (!free.length) return;
		var href = lessonUrl(where.course, free[0].number);
		var label = free.length === 1 ? "Try the free lesson" : "Try the free lessons";
		var cards = document.querySelectorAll(CARD);
		for (var i = 0; i < cards.length; i++) {
			var buy = cards[i].querySelector('a[href*="/billing/course/"]');
			place(
				"free-lessons-" + i,
				buy,
				"after",
				function () {
					return element("a", "al-btn al-btn-light al-free-lessons");
				},
				function (node) {
					setAttr(node, "href", href);
					setText(node, label);
				}
			);
		}
	}

	// The reading column of a lesson that the learner may open.
	function readingColumn() {
		return document.querySelector(".bg-surface-base.min-w-0 > div > .px-5");
	}

	// The app's own control to the next lesson: the phone bar's button, or the desktop "Next" beside
	// the title. The app shows it only when it allows the move, for example after a quiz is passed.
	function appNext() {
		var phone = document.querySelector('button[aria-label="Next lesson"]');
		if (phone) return phone;
		var column = readingColumn();
		var title = column ? column.querySelector("h1.text-4xl-semibold") : null;
		var row = title && title.parentNode ? title.parentNode.parentNode : null;
		var icons = row ? row.querySelectorAll(".lucide-chevron-right") : [];
		for (var i = 0; i < icons.length; i++) {
			var button = icons[i].closest("button");
			if (button && row.contains(button)) return button;
		}
		return null;
	}

	// A "Next lesson" card after the lesson text. A click goes through the app's own Next.
	function nextLesson(where, outline) {
		if (where.page !== "lesson" || !where.number || !outline) return;
		var current = outline.byNumber[where.number];
		var next = current ? outline.lessons[outline.lessons.indexOf(current) + 1] : null;
		var column = readingColumn();
		if (!next || !column || !appNext()) return;
		var anchor = null;
		for (var i = 0; i < column.children.length; i++) {
			var child = column.children[i];
			if (child.classList.contains("ProseMirror") && child.classList.contains("prose")) anchor = child;
		}
		var note = offered(where.course) && !next.preview ? "Opens when you buy" : "";
		place(
			"next-lesson",
			anchor,
			"after",
			function () {
				var card = element("button", "al-next");
				card.type = "button";
				var text = element("span");
				text.appendChild(element("span", "al-next-label", "Next lesson"));
				text.appendChild(element("span", "al-next-title"));
				card.appendChild(text);
				var arrow = element("span", "al-next-arrow");
				arrow.setAttribute("aria-hidden", "true");
				card.appendChild(arrow);
				card.addEventListener("click", function () {
					var control = appNext();
					if (control) control.click();
				});
				return card;
			},
			function (card) {
				var text = card.firstChild;
				setText(text.querySelector(".al-next-title"), next.title);
				var line = text.querySelector(".al-next-note");
				if (note && !line) line = text.appendChild(element("span", "al-next-note"));
				if (note) setText(line, note);
				else if (line) text.removeChild(line);
			}
		);
	}

	// "N of M lessons done" in the course card of an enrolled learner.
	function progressLine(where, outline) {
		if (where.page !== "course" || !enrolled[where.course] || !outline || !outline.progress) return;
		var total = outline.lessons.length;
		var done = outline.lessons.filter(function (lesson) {
			return lesson.complete;
		}).length;
		var width = Math.round((done / total) * 1000) / 10 + "%";
		var cards = document.querySelectorAll(CARD);
		for (var i = 0; i < cards.length; i++) {
			var link = cards[i].querySelector('a[href*="/learn/"]');
			var block = link && link.parentNode !== cards[i] ? link.parentNode : link;
			place(
				"progress-" + i,
				block,
				"before",
				function () {
					var node = element("div", "al-progress");
					node.appendChild(element("span", "al-progress-text"));
					var track = element("span", "al-progress-track");
					track.appendChild(element("span"));
					node.appendChild(track);
					return node;
				},
				function (node) {
					setText(node.firstChild, done + " of " + total + " lessons done");
					var bar = node.lastChild.firstChild;
					if (bar.style.width !== width) bar.style.width = width;
				}
			);
		}
	}

	var watcher = null;
	var watched = null;

	function stopWatching() {
		if (watcher) watcher.disconnect();
		watcher = null;
		watched = null;
		setAttr(root, "data-al-actionbar", null);
	}

	// The action of the course card in the page on a phone: Buy, Enroll Now or Continue Learning.
	function cardAction() {
		var cards = document.querySelectorAll(CARD);
		for (var i = 0; i < cards.length; i++) {
			if (cards[i].closest("aside")) continue;
			var button = cards[i].querySelector(".p-5 button.w-full");
			if (button) return { card: cards[i], button: button, target: button.closest("a") || button };
		}
		return null;
	}

	// On phones, a bar with the price and the card's action, shown while that action is out of view.
	function actionBar(where) {
		var nav = document.querySelector('nav[aria-label="Primary"]');
		var found =
			where.page === "course" && !config.staff && nav && window.IntersectionObserver ? cardAction() : null;
		if (!found) {
			stopWatching();
			return;
		}
		var price = "";
		if (!enrolled[where.course]) {
			var amount = found.card.querySelector(".p-5 > .text-3xl-semibold:first-child");
			price = amount ? amount.textContent.trim() : "";
		}
		var label = found.button.textContent.trim();
		var bottom = Math.round(nav.getBoundingClientRect().height) + "px";
		place(
			"action-bar",
			document.body,
			"inside",
			function () {
				var bar = element("div", "al-actionbar");
				bar.appendChild(element("span", "al-actionbar-price"));
				var button = element("button", "al-btn al-actionbar-action");
				button.type = "button";
				button.addEventListener("click", function () {
					var action = cardAction();
					if (action) action.button.click();
				});
				bar.appendChild(button);
				return bar;
			},
			function (bar) {
				var amount = bar.firstChild;
				setText(amount, price);
				if (amount.hidden !== !price) amount.hidden = !price;
				setText(bar.lastChild, label);
				if (bar.style.bottom !== bottom) bar.style.bottom = bottom;
			}
		);
		if (watched !== found.target) {
			if (watcher) watcher.disconnect();
			watched = found.target;
			watcher = new window.IntersectionObserver(function (entries) {
				var entry = entries[entries.length - 1];
				setAttr(root, "data-al-actionbar", entry && !entry.isIntersecting ? "1" : "0");
			});
			watcher.observe(found.target);
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

	// One step of the refresh. A step that fails leaves its part of the page as the Learning app drew it.
	function safely(step, where, outline) {
		try {
			step(where, outline);
		} catch (error) {
			if (window.console && window.console.error) window.console.error("activationlab:", error);
		}
	}

	function update() {
		var where = mark();
		relabel();
		var outline = where.page === "course" || where.page === "lesson" ? outlineOf(where.course) : null;
		kept = {};
		safely(chipsAndLocks, where, outline);
		safely(freeLessons, where, outline);
		safely(nextLesson, where, outline);
		safely(progressLine, where, outline);
		safely(actionBar, where, outline);
		sweep();
		kept = null;
	}

	var pending = false;
	function refresh() {
		if (pending) return;
		pending = true;
		var run = function () {
			pending = false;
			update();
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
