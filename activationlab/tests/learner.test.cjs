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
function load(url, config = CONFIG) {
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
