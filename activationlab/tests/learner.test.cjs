// Tests for public/js/learner.js in a simulated browser (jsdom).
// dev/test.sh runs them with "node --test" after the Python tests.
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { JSDOM, VirtualConsole } = require("jsdom");

const SCRIPT = fs.readFileSync(path.join(__dirname, "..", "public", "js", "learner.js"), "utf8");
const BUY = "/api/method/activationlab.checkout.buy";
const CONFIG = {
	lmsPath: "lms",
	buyUrl: BUY,
	staff: false,
	paidCourses: ["paid-course", "owned-course"],
	enrolled: ["owned-course"],
};

// The card the Learning app shows on a lesson the learner may not open (pages/Lesson.vue).
const LOCKED_CARD = `<div class="shadow rounded-md w-3/4 mt-10 mx-auto text-center p-4">
	<div class="flex items-center"><span class="lucide-lock-keyhole size-4"></span>
	<div class="text-lg-semibold">This lesson is locked</div></div>
	<button type="button"><span class="truncate">Start Learning</span></button></div>`;

// The notice for a lesson that is locked until the ones before it are done (LockedLessonNotice.vue).
const SEQUENCE_NOTICE = `<div class="p-5"><div class="flex items-center gap-3 rounded-lg bg-surface-amber-2 p-3">
	<span class="lucide-lock-keyhole size-4"></span><button type="button"><span class="truncate">Go now</span></button>
	</div></div>`;

// Load the script into a page at url. Page navigations are recorded in `assigned` instead of made.
// extras go onto the window before the script runs, such as a stand-in for fetch.
function load(url, config = CONFIG, extras = {}) {
	// jsdom cannot follow a link. A click that the script leaves to the browser tries to, which is expected.
	const virtualConsole = new VirtualConsole();
	virtualConsole.on("jsdomError", (error) => {
		if (!/Not implemented: navigation/.test(error.message)) console.error(error);
	});
	const dom = new JSDOM("<!DOCTYPE html><html><head></head><body></body></html>", {
		url,
		pretendToBeVisual: true,
		virtualConsole,
	});
	const win = dom.window;
	const assigned = [];
	const location = {
		get href() {
			return win.location.href;
		},
		get pathname() {
			return win.location.pathname;
		},
		get origin() {
			return win.location.origin;
		},
		assign(to) {
			assigned.push(to);
		},
	};
	const windowView = new Proxy(win, {
		get(target, key) {
			if (key === "location") return location;
			const value = Reflect.get(target, key);
			return typeof value === "function" && !/^[A-Z]/.test(String(key)) ? value.bind(target) : value;
		},
		set(target, key, value) {
			return Reflect.set(target, key, value);
		},
	});
	win.activationlab = config;
	Object.assign(win, extras);
	const context = vm.createContext({
		window: windowView,
		document: win.document,
		NodeFilter: win.NodeFilter,
		URL: win.URL,
		MutationObserver: win.MutationObserver,
		setTimeout,
	});
	vm.runInContext(SCRIPT, context);
	return { win, doc: win.document, root: win.document.documentElement, assigned, run: () => vm.runInContext(SCRIPT, context) };
}

function click(win, node, options = {}) {
	const event = new win.MouseEvent("click", { bubbles: true, cancelable: true, button: 0, ...options });
	node.dispatchEvent(event);
	return event;
}

function render(page, html) {
	page.doc.body.insertAdjacentHTML("beforeend", html);
	return new Promise((resolve) => setTimeout(resolve, 60));
}

test("marks the page kind, the course and enrolment", () => {
	const page = load("https://platform.test/lms/courses/owned-course");
	assert.strictEqual(page.root.getAttribute("data-al-page"), "course");
	assert.strictEqual(page.root.getAttribute("data-al-course"), "owned-course");
	assert.strictEqual(page.root.getAttribute("data-al-enrolled"), "1");
	assert.strictEqual(page.root.getAttribute("data-al-staff"), null);

	page.win.history.pushState({}, "", "/lms/courses/paid-course/learn/1-3");
	assert.strictEqual(page.root.getAttribute("data-al-page"), "lesson");
	assert.strictEqual(page.root.getAttribute("data-al-enrolled"), "0");

	page.win.history.pushState({}, "", "/lms");
	assert.strictEqual(page.root.getAttribute("data-al-page"), "home");
	assert.strictEqual(page.root.getAttribute("data-al-course"), null);

	page.win.history.replaceState({}, "", "/lms/courses");
	assert.strictEqual(page.root.getAttribute("data-al-page"), "courses");
});

test("marks staff", () => {
	const page = load("https://platform.test/lms/courses", { ...CONFIG, staff: true });
	assert.strictEqual(page.root.getAttribute("data-al-staff"), "1");
});

test("a move to a billing page goes to the checkout instead", () => {
	const page = load("https://platform.test/lms/courses/paid-course");
	page.win.history.pushState({}, "", "/lms/billing/course/paid-course");
	page.win.history.replaceState({}, "", "https://platform.test/lms/billing/course/paid%20course");
	assert.deepStrictEqual(page.assigned, [`${BUY}?course=paid-course`, `${BUY}?course=paid%20course`]);
	assert.strictEqual(page.win.location.pathname, "/lms/courses/paid-course");

	page.win.history.pushState({}, "", "/lms/billing/certificate/paid-course");
	assert.strictEqual(page.assigned.length, 2);
});

test("a click on a billing link goes to the checkout and never reaches the app", () => {
	const page = load("https://platform.test/lms/courses/paid-course");
	page.doc.body.innerHTML =
		'<a href="/lms/billing/course/paid-course"><button><span class="truncate">Buy this course</span></button></a>';
	let appSawIt = false;
	page.doc.querySelector("a").addEventListener("click", () => (appSawIt = true));

	const event = click(page.win, page.doc.querySelector("span"));

	assert.deepStrictEqual(page.assigned, [`${BUY}?course=paid-course`]);
	assert.ok(event.defaultPrevented);
	assert.strictEqual(appSawIt, false);
});

test("a click with a modifier key is left to the browser", () => {
	const page = load("https://platform.test/lms/courses/paid-course");
	page.doc.body.innerHTML = '<a href="/lms/billing/course/paid-course">Buy</a>';
	const event = click(page.win, page.doc.querySelector("a"), { metaKey: true });
	assert.deepStrictEqual(page.assigned, []);
	assert.strictEqual(event.defaultPrevented, false);
});

test("a locked lesson of a paid course offers the purchase", async () => {
	const page = load("https://platform.test/lms/courses/paid-course/learn/1-3");
	await render(page, LOCKED_CARD);

	const button = page.doc.querySelector("button");
	assert.strictEqual(button.textContent.trim(), "Buy this course");
	click(page.win, button.querySelector("span"));
	assert.deepStrictEqual(page.assigned, [`${BUY}?course=paid-course`]);
});

test("a locked lesson of an enrolled or free course is left alone", async () => {
	for (const url of [
		"https://platform.test/lms/courses/owned-course/learn/1-3",
		"https://platform.test/lms/courses/free-course/learn/1-3",
	]) {
		const page = load(url);
		await render(page, LOCKED_CARD);
		const button = page.doc.querySelector("button");
		assert.strictEqual(button.textContent.trim(), "Start Learning");
		const event = click(page.win, button);
		assert.deepStrictEqual(page.assigned, []);
		assert.strictEqual(event.defaultPrevented, false);
	}
});

test("the notice of a lesson locked by order is left alone", async () => {
	const page = load("https://platform.test/lms/courses/paid-course/learn/1-3");
	await render(page, SEQUENCE_NOTICE);
	const button = page.doc.querySelector("button");
	assert.strictEqual(button.textContent.trim(), "Go now");
	click(page.win, button);
	assert.deepStrictEqual(page.assigned, []);
});

test("a second copy of the script does nothing", () => {
	const page = load("https://platform.test/lms/courses/paid-course");
	page.run();
	page.win.history.pushState({}, "", "/lms/billing/course/paid-course");
	assert.deepStrictEqual(page.assigned, [`${BUY}?course=paid-course`]);
});

test("a page without the configuration is left alone", () => {
	const page = load("https://platform.test/lms/courses/paid-course", null);
	assert.strictEqual(page.root.getAttribute("data-al-page"), null);
});

// The outline that lms.lms.utils.get_course_outline returns for a course of two chapters, five lessons,
// the first two free. With progress=1 the first two lessons are done.
function outlineFor(url) {
	const progress = /[?&]progress=1/.test(url);
	const lesson = (number, title, preview, done) => ({
		name: `lesson-${number}`,
		number,
		title,
		include_in_preview: preview ? 1 : 0,
		...(progress ? { is_complete: done } : {}),
	});
	return [
		{ name: "chapter-1", title: "Start with the job", idx: 1, lessons: [lesson("1-1", "What an agent is", 1, true), lesson("1-2", "Pick the first job", 1, true)] },
		{
			name: "chapter-2",
			title: "Hand over the work",
			idx: 2,
			lessons: [lesson("2-1", "Write a work brief", 0, false), lesson("2-2", "Set the rules", 0, false), lesson("2-3", "Run a pilot", 0, false)],
		},
	];
}

// A stand-in for fetch that records each address and answers with reply(url).
function stubFetch(reply = (url) => ({ message: outlineFor(url) })) {
	const calls = [];
	const fetch = (url) => {
		calls.push(String(url));
		const answer = reply(String(url));
		if (answer instanceof Error) return Promise.reject(answer);
		if (answer === null) return Promise.resolve({ ok: false, json: () => Promise.resolve({}) });
		return Promise.resolve({ ok: true, json: () => Promise.resolve(answer) });
	};
	return { calls, fetch };
}

// A stand-in for IntersectionObserver. show(false) reports the watched element out of view.
function stubObserver() {
	const made = [];
	class Observer {
		constructor(callback) {
			this.callback = callback;
			this.targets = [];
			made.push(this);
		}
		observe(target) {
			this.targets.push(target);
		}
		disconnect() {
			this.targets = [];
		}
		show(visible) {
			this.callback(this.targets.map((target) => ({ target, isIntersecting: visible })));
		}
	}
	return { made, Observer };
}

function settle() {
	return new Promise((resolve) => setTimeout(resolve, 80));
}

// The course card (CourseCardOverlay.vue) with a buy button, or with "Continue Learning".
function buyCard(course) {
	return `<div class="border-2 rounded-md min-w-80 max-w-sm"><div class="p-5"><div class="text-3xl-semibold text-ink-gray-9 mb-4">$ 299</div>
	<div><a href="/lms/billing/course/${course}"><button type="button" class="w-full mb-8 inline-flex rounded-4 bg-surface-gray-10">
	<span class="lucide-credit-card size-4"></span><span class="truncate"><span>Buy this course</span></span></button></a></div>
	<section class="space-y-3"><div class="text-base">This course includes:</div></section></div></div>`;
}

function continueCard(course) {
	return `<div class="border-2 rounded-md min-w-80 max-w-sm"><div class="p-5"><div class="text-3xl-semibold text-ink-gray-9 mb-4">$ 299</div>
	<div><div class="space-y-2 mb-8"><a href="/lms/courses/${course}/learn/2-1"><button type="button" class="w-full inline-flex rounded-4 bg-surface-gray-10">
	<span class="lucide-book-text size-4"></span><span class="truncate"><span>Continue Learning</span></span></button></a></div></div>
	<section class="space-y-3"><div class="text-base">This course includes:</div></section></div></div>`;
}

// One chapter of the course outline (ChapterRow.vue), open or closed.
function chapterItem(course, title, numbers, open) {
	const rows = numbers
		.map(
			(number) => `<div class="outline-lesson ps-8 py-2 pe-4"><a href="/lms/courses/${course}/learn/${number}" id="row-${number}">
			<div class="flex items-center text-sm leading-5 group"><span class="lucide-file-text h-4 w-4 me-2"></span>Lesson ${number}</div></a></div>`
		)
		.join("");
	return `<div class="chapter-item"><button type="button" class="flex items-center w-full p-2 group"><span class="lucide-chevron-right size-4"></span>
	<div class="ms-2 min-w-0 flex-1 text-start flex items-baseline justify-between gap-3"><div class="truncate text-base-medium">${title}</div></div>
	<div class="flex ms-3 items-center gap-x-4 shrink-0"><span class="text-sm text-ink-gray-5">${numbers.length}</span></div></button>
	${open ? `<div><div data-chapter="${title}">${rows}</div></div>` : ""}</div>`;
}

// The course page of a learner (CourseOverview.vue): the card in the hero for phones and in the aside.
function coursePage(course, card = buyCard) {
	return `<div class="p-5"><div class="flex flex-col md:flex-row"><div class="md:w-2/3">
	<section><h1 class="text-4xl-semibold">Course</h1><div class="md:hidden" id="inline-card">${card(course)}</div></section>
	<section><div class="border rounded-md p-2"><div class="p-3 h-full"><div><div>
	${chapterItem(course, "Start with the job", ["1-1", "1-2"], true)}${chapterItem(course, "Hand over the work", ["2-1", "2-2", "2-3"], true)}
	</div></div></div></div></section></div>
	<aside class="hidden md:flex" id="aside">${card(course)}</aside></div></div>`;
}

// The phone tab bar (MobileLayout.vue).
const TAB_BAR = `<div class="relative z-20 shrink-0"><nav aria-label="Primary"><button type="button" aria-current="page"><svg></svg><span>Courses</span></button></nav></div>`;

// A lesson the learner may open (Lesson.vue), with the outline beside it (StudentLessonSidebar.vue).
function lessonPage(course, { next = true } = {}) {
	const nextButton = next
		? `<button type="button" class="inline-flex rounded-4 bg-surface-gray-2" id="app-next"><span class="truncate"><span>Next</span></span><span class="lucide-chevron-right size-4"></span></button>`
		: `<a href="/lms/courses/${course}"><button type="button" class="inline-flex rounded-4 bg-surface-gray-2">Back to Course</button></a>`;
	const rows = ["1-1", "1-2", "2-1", "2-2", "2-3"]
		.map((number) => `<li><a href="/lms/courses/${course}/learn/${number}" id="side-${number}"><svg class="lucide lucide-file-text-icon"></svg><span class="truncate flex-1">Lesson ${number}</span><svg class="lucide lucide-circle-icon"></svg></a></li>`)
		.join("");
	return `<div class="grid md:grid-cols-[70%,30%]"><div class="bg-surface-base min-w-0"><div class="sm:border-e pt-8 pb-10 h-full"><div class="px-5" id="column">
	<div class="flex flex-col md:flex-row justify-between"><div class="flex flex-col"><h1 class="text-4xl-semibold">Lesson</h1></div>
	<div class="flex items-center gap-x-2"><button type="button" class="inline-flex rounded-4 bg-surface-gray-2"><span class="lucide-chevron-left size-4"></span><span class="truncate"><span>Previous</span></span></button>${nextButton}</div></div>
	<div class="flex items-center mt-4"><span class="h-6 me-1"></span></div>
	<div class="ProseMirror prose prose-sm max-w-none mt-8" id="text"><div id="editor"><p>Text.</p></div></div></div>
	<div class="mt-10 pb-20 pt-5 border-t px-5"><div class="tiptap prose ProseMirror" id="notes"></div></div></div></div>
	<aside class="sticky top-10 h-[94vh]"><div class="flex flex-col h-full"><div class="bg-surface-gray-1 px-5 py-5 border-b"><div class="text-lg-semibold">Course</div></div>
	<ul class="flex-1 list-none"><li><button type="button">Chapter</button><div><ul class="list-none">${rows}</ul></div></li></ul></div></aside></div>`;
}

function added(page, key) {
	return page.doc.querySelectorAll(`[data-al-added="${key}"]`);
}

function label(page) {
	return page.root.style.getPropertyValue("--al-lesson-label");
}

test("marks the profile and the You page", () => {
	const page = load("https://platform.test/lms/user/learner");
	assert.strictEqual(page.root.getAttribute("data-al-page"), "profile");
	page.win.history.pushState({}, "", "/lms/you");
	assert.strictEqual(page.root.getAttribute("data-al-page"), "you");
	page.win.history.pushState({}, "", "/lms/user/learner/certificates");
	assert.strictEqual(page.root.getAttribute("data-al-page"), "other");
});

test("marks whether the learner has courses and whether the course is paid", () => {
	const page = load("https://platform.test/lms/courses/paid-course");
	assert.strictEqual(page.root.getAttribute("data-al-courses"), "1");
	assert.strictEqual(page.root.getAttribute("data-al-paid"), "1");
	page.win.history.pushState({}, "", "/lms/courses/free-course");
	assert.strictEqual(page.root.getAttribute("data-al-paid"), null);
	page.win.history.pushState({}, "", "/lms");
	assert.strictEqual(page.root.getAttribute("data-al-paid"), null);

	const newcomer = load("https://platform.test/lms", { ...CONFIG, enrolled: [] });
	assert.strictEqual(newcomer.root.getAttribute("data-al-courses"), "0");
});

test("a lesson carries its chapter and lesson numbers, and Free preview on a free lesson of a course on offer", async () => {
	const { fetch } = stubFetch();
	const page = load("https://platform.test/lms/courses/paid-course/learn/1-1", CONFIG, { fetch });
	assert.strictEqual(label(page), '"Chapter 1 · Lesson 1"');
	await settle();
	assert.strictEqual(label(page), '"Free preview · Chapter 1 · Lesson 1"');

	page.win.history.pushState({}, "", "/lms/courses/paid-course/learn/2-1");
	assert.strictEqual(label(page), '"Chapter 2 · Lesson 1"');

	page.win.history.pushState({}, "", "/lms/courses/owned-course/learn/1-1");
	await settle();
	assert.strictEqual(label(page), '"Chapter 1 · Lesson 1"');

	page.win.history.pushState({}, "", "/lms/courses/paid-course");
	assert.strictEqual(label(page), "");
});

test("the outline is read once per course, with progress only for an enrolled course", async () => {
	const { calls, fetch } = stubFetch();
	const page = load("https://platform.test/lms/courses/paid-course", CONFIG, { fetch });
	await settle();
	page.win.history.pushState({}, "", "/lms/courses/paid-course/learn/1-1");
	await settle();
	page.win.history.pushState({}, "", "/lms/courses");
	await settle();
	page.win.history.pushState({}, "", "/lms/courses/owned-course");
	await settle();
	page.win.history.pushState({}, "", "/lms/courses/owned-course/learn/2-1");
	await settle();
	assert.deepStrictEqual(calls, [
		"/api/method/lms.lms.utils.get_course_outline?course=paid-course",
		"/api/method/lms.lms.utils.get_course_outline?course=owned-course&progress=1",
	]);
});

test("a failed or malformed outline adds nothing and is not read again", async () => {
	for (const reply of [() => null, () => ({ message: { chapters: [] } }), () => ({ message: [{ title: "No lessons" }] }), () => new Error("offline")]) {
		const { calls, fetch } = stubFetch(reply);
		const page = load("https://platform.test/lms/courses/paid-course", CONFIG, { fetch });
		await render(page, coursePage("paid-course"));
		await settle();
		assert.strictEqual(page.doc.querySelectorAll("[data-al-added], [data-al-chip], [data-al-locked]").length, 0);
		page.win.history.pushState({}, "", "/lms/courses/paid-course/learn/1-1");
		await settle();
		assert.strictEqual(calls.length, 1);
		assert.strictEqual(label(page), '"Chapter 1 · Lesson 1"');
	}
});

test("chips and locks show what opens after purchase, only for a paid course on offer", async () => {
	const { fetch } = stubFetch();
	const page = load("https://platform.test/lms/courses/paid-course", CONFIG, { fetch });
	await render(page, coursePage("paid-course"));
	await settle();
	const chips = [...page.doc.querySelectorAll(".chapter-item")].map((item) => item.getAttribute("data-al-chip"));
	assert.deepStrictEqual(chips, ["preview", "locked"]);
	const locked = [...page.doc.querySelectorAll("[data-al-locked]")].map((link) => link.id);
	assert.deepStrictEqual(locked, ["row-2-1", "row-2-2", "row-2-3"]);

	const lesson = load("https://platform.test/lms/courses/paid-course/learn/1-1", CONFIG, { fetch });
	await render(lesson, lessonPage("paid-course"));
	await settle();
	const sideLocked = [...lesson.doc.querySelectorAll("[data-al-locked]")].map((link) => link.id);
	assert.deepStrictEqual(sideLocked, ["side-2-1", "side-2-2", "side-2-3"]);

	const cases = [
		["owned-course", CONFIG],
		["free-course", CONFIG],
		["paid-course", { ...CONFIG, staff: true }],
	];
	for (const [course, config] of cases) {
		const other = load(`https://platform.test/lms/courses/${course}`, config, stubFetch());
		await render(other, coursePage(course));
		await settle();
		assert.strictEqual(other.doc.querySelectorAll("[data-al-chip], [data-al-locked]").length, 0, course);
	}
});

test("Try the free lessons links to the first free lesson, once per card, and goes after enrolment", async () => {
	const { fetch } = stubFetch();
	const page = load("https://platform.test/lms/courses/paid-course", CONFIG, { fetch });
	await render(page, coursePage("paid-course"));
	await settle();
	const links = page.doc.querySelectorAll(".al-free-lessons");
	assert.strictEqual(links.length, 2);
	for (const link of links) {
		assert.strictEqual(link.getAttribute("href"), "/lms/courses/paid-course/learn/1-1");
		assert.strictEqual(link.textContent, "Try the free lessons");
		assert.strictEqual(link.previousElementSibling.getAttribute("href"), "/lms/billing/course/paid-course");
	}
	// The card's price stays: the added link is not the enrolled learner's link to a lesson.
	assert.strictEqual(page.doc.querySelectorAll('.al-free-lessons[data-al-added]').length, 2);

	// The app draws the card again after enrolment, with "Continue Learning" in place of the buy button.
	for (const id of ["inline-card", "aside"]) page.doc.getElementById(id).innerHTML = continueCard("paid-course");
	await settle();
	assert.strictEqual(page.doc.querySelectorAll(".al-free-lessons").length, 0);
});

test("the Next lesson card appears with the app's Next and a next lesson, once, and its click is the app's", async () => {
	const { fetch } = stubFetch();
	const page = load("https://platform.test/lms/courses/owned-course/learn/2-1", CONFIG, { fetch });
	await render(page, lessonPage("owned-course"));
	await settle();
	let appClicks = 0;
	page.doc.getElementById("app-next").addEventListener("click", () => appClicks++);
	const cards = added(page, "next-lesson");
	assert.strictEqual(cards.length, 1);
	const card = cards[0];
	assert.strictEqual(card.previousElementSibling.id, "text");
	assert.strictEqual(card.querySelector(".al-next-title").textContent, "Set the rules");
	assert.strictEqual(card.querySelector(".al-next-note"), null);
	click(page.win, card.querySelector(".al-next-title"));
	assert.strictEqual(appClicks, 1);

	// The app moves the lesson text: the card follows it.
	page.doc.getElementById("column").appendChild(page.doc.getElementById("text"));
	await settle();
	assert.strictEqual(added(page, "next-lesson").length, 1);
	assert.strictEqual(added(page, "next-lesson")[0].previousElementSibling.id, "text");

	// Without the app's Next, as before a quiz is passed, there is no card.
	const waiting = load("https://platform.test/lms/courses/owned-course/learn/2-1", CONFIG, stubFetch());
	await render(waiting, lessonPage("owned-course", { next: false }));
	await settle();
	assert.strictEqual(added(waiting, "next-lesson").length, 0);

	// The last lesson has no next lesson.
	const last = load("https://platform.test/lms/courses/owned-course/learn/2-3", CONFIG, stubFetch());
	await render(last, lessonPage("owned-course"));
	await settle();
	assert.strictEqual(added(last, "next-lesson").length, 0);
});

test("the Next lesson card says when the next lesson opens only after purchase", async () => {
	const page = load("https://platform.test/lms/courses/paid-course/learn/1-2", CONFIG, stubFetch());
	await render(page, lessonPage("paid-course"));
	await settle();
	const card = added(page, "next-lesson")[0];
	assert.strictEqual(card.querySelector(".al-next-title").textContent, "Write a work brief");
	assert.strictEqual(card.querySelector(".al-next-note").textContent, "Opens when you buy");
});

test("an enrolled learner's card shows the lessons done", async () => {
	const page = load("https://platform.test/lms/courses/owned-course", CONFIG, stubFetch());
	await render(page, coursePage("owned-course", continueCard));
	await settle();
	const lines = page.doc.querySelectorAll(".al-progress");
	assert.strictEqual(lines.length, 2);
	for (const line of lines) {
		assert.strictEqual(line.querySelector(".al-progress-text").textContent, "2 of 5 lessons done");
		assert.strictEqual(line.querySelector(".al-progress-track > span").style.width, "40%");
		assert.ok(line.nextElementSibling.querySelector('a[href*="/learn/"]'));
	}
	// No line for a learner who has not bought the course.
	const visitor = load("https://platform.test/lms/courses/paid-course", CONFIG, stubFetch());
	await render(visitor, coursePage("paid-course"));
	await settle();
	assert.strictEqual(visitor.doc.querySelectorAll(".al-progress").length, 0);
});

test("on a phone the action bar offers the card's action while it is out of view", async () => {
	const observer = stubObserver();
	const page = load("https://platform.test/lms/courses/paid-course", CONFIG, { ...stubFetch(), IntersectionObserver: observer.Observer });
	await render(page, coursePage("paid-course") + TAB_BAR);
	await settle();
	const bars = added(page, "action-bar");
	assert.strictEqual(bars.length, 1);
	const bar = bars[0];
	assert.strictEqual(bar.parentNode, page.doc.body);
	assert.strictEqual(bar.querySelector(".al-actionbar-price").textContent, "$ 299");
	assert.strictEqual(bar.querySelector("button").textContent, "Buy this course");
	// It watches the card in the page, not the one in the aside.
	assert.ok(page.doc.getElementById("inline-card").contains(observer.made[0].targets[0]));

	observer.made[0].show(false);
	assert.strictEqual(page.root.getAttribute("data-al-actionbar"), "1");
	observer.made[0].show(true);
	assert.strictEqual(page.root.getAttribute("data-al-actionbar"), "0");

	// Its click is the card's own: a buy button goes to the checkout.
	click(page.win, bar.querySelector("button"));
	assert.deepStrictEqual(page.assigned, [`${BUY}?course=paid-course`]);

	// It goes away on another page.
	page.win.history.pushState({}, "", "/lms/courses");
	await settle();
	assert.strictEqual(added(page, "action-bar").length, 0);
	assert.strictEqual(page.root.getAttribute("data-al-actionbar"), null);
});

test("the action bar needs the phone tab bar and a card action, and is not for staff", async () => {
	const cases = [
		[CONFIG, coursePage("paid-course")],
		[CONFIG, `<div>No card</div>${TAB_BAR}`],
		[{ ...CONFIG, staff: true }, coursePage("paid-course") + TAB_BAR],
	];
	for (const [config, html] of cases) {
		const observer = stubObserver();
		const page = load("https://platform.test/lms/courses/paid-course", config, { ...stubFetch(), IntersectionObserver: observer.Observer });
		await render(page, html);
		await settle();
		assert.strictEqual(added(page, "action-bar").length, 0);
		assert.strictEqual(observer.made.length, 0);
	}
	// An enrolled learner's bar shows "Continue Learning" and no price.
	const observer = stubObserver();
	const learner = load("https://platform.test/lms/courses/owned-course", CONFIG, { ...stubFetch(), IntersectionObserver: observer.Observer });
	await render(learner, coursePage("owned-course", continueCard) + TAB_BAR);
	await settle();
	const bar = added(learner, "action-bar")[0];
	assert.strictEqual(bar.querySelector("button").textContent, "Continue Learning");
	assert.strictEqual(bar.querySelector(".al-actionbar-price").hidden, true);
	let appClicks = 0;
	learner.doc.querySelector('#inline-card a[href*="/learn/"]').addEventListener("click", (event) => {
		event.preventDefault();
		appClicks++;
	});
	click(learner.win, bar.querySelector("button"));
	assert.strictEqual(appClicks, 1);
});

test("a second refresh changes nothing on the page", async () => {
	const observer = stubObserver();
	const pages = [
		["https://platform.test/lms/courses/paid-course", coursePage("paid-course") + TAB_BAR],
		["https://platform.test/lms/courses/owned-course", coursePage("owned-course", continueCard)],
		["https://platform.test/lms/courses/paid-course/learn/1-2", lessonPage("paid-course")],
	];
	for (const [url, html] of pages) {
		const page = load(url, CONFIG, { ...stubFetch(), IntersectionObserver: observer.Observer });
		await render(page, html);
		await settle();
		const before = page.doc.documentElement.outerHTML;
		const changes = [];
		new page.win.MutationObserver((records) => changes.push(...records)).observe(page.doc.documentElement, {
			attributes: true,
			childList: true,
			subtree: true,
			characterData: true,
		});
		page.win.history.replaceState({}, "", page.win.location.pathname);
		await settle();
		assert.strictEqual(changes.length, 0, url);
		assert.strictEqual(page.doc.documentElement.outerHTML, before, url);
	}
});

test("a page without the configuration gets no additions", async () => {
	const { calls, fetch } = stubFetch();
	const page = load("https://platform.test/lms/courses/paid-course", null, { fetch });
	await render(page, coursePage("paid-course") + TAB_BAR);
	await settle();
	assert.strictEqual(page.doc.querySelectorAll("[data-al-added], [data-al-chip], [data-al-locked]").length, 0);
	assert.strictEqual(calls.length, 0);
});
