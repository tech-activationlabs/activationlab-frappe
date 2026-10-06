// Tests for public/css/learner.css in a simulated browser (jsdom): what it hides and what it keeps.
// The markup copies the Learning app's components at version 2.63.0, with the classes they render.
// dev/test.sh runs them with "node --test" after the Python tests.
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const CSS = fs.readFileSync(path.join(__dirname, "..", "public", "css", "learner.css"), "utf8");

const SIDEBAR = `<div class="flex h-full flex-col justify-between border-e bg-surface-sidebar overflow-x-hidden w-56">
	<div class="flex flex-col gap-3 text-ink-gray-9 py-2.5 px-3 bg-surface-base shadow-sm rounded-md" id="profile">
		<div class="flex flex-col text-p-sm gap-1"><div class="inline-flex gap-1">
		<span class="lucide-user h-4 my-0.5 shrink-0"></span><div class="font-medium">Complete your profile</div></div></div>
		<a href="/lms/user/learner"><button>My Profile</button></a></div>
	<a href="/lms/user/learner" class="flex items-center justify-center" id="profile-collapsed"><span class="lucide-user size-4"></span></a>
	<div class="flex items-center gap-2"><span class="lucide-circle-help size-4" id="help"></span>
	<span class="lucide-zap size-4 text-ink-gray-7 cursor-pointer" id="powered"></span></div></div>`;

const HOME = `<div class="w-full p-5"><div class="flex items-center justify-between">
	<h1 class="text-2xl-bold text-ink-gray-9" id="greeting">Hey, Learner</h1>
	<div><button type="button" aria-label="View learning streak: 3 days" class="bg-surface-amber-2 px-2 py-1 rounded-md cursor-pointer" id="streak">
	<span>3</span></button></div></div></div>`;

const COURSE = `<header class="header-frame sticky top-0 z-10 justify-between"><div class="flex min-w-0 flex-1 items-center gap-2">
	<nav>Courses</nav><div class="inline-flex select-none items-center gap-1 overflow-clip rounded-full whitespace-nowrap text-ink-green-8 bg-surface-green-2" id="published">Published</div></div></header>
	<div class="p-5"><div class="flex flex-col md:flex-row items-start justify-between w-full">
	<div class="md:w-2/3 space-y-10 min-w-0">
		<section class="space-y-4"><h1 class="text-4xl-semibold text-ink-gray-9" id="title">Course</h1>
		<div class="flex flex-wrap items-center gap-x-3 gap-y-2 text-ink-gray-7">
			<div class="flex items-center gap-1.5" id="students"><span class="lucide-users-round size-4"></span><span>12 Students</span></div>
			<span class="lucide-dot size-5 text-ink-gray-7" id="students-dot"></span>
			<div class="flex items-center" id="instructors"><span>Instructor</span></div></div></section>
		<section><h2 class="text-3xl-semibold text-ink-gray-9" id="content-heading">Course content</h2></section>
		REVIEWS
	</div>
	<aside class="hidden md:flex w-80 shrink-0 flex-col"><div class="border-2 rounded-md min-w-80 max-w-sm"><div class="p-5">
		<div class="text-3xl-semibold text-ink-gray-9 mb-4" id="price">$299.00</div>
		<div>CARD_ACTION</div>
		<section class="space-y-3"><div class="text-base">This course includes:</div>
		<div class="flex items-center gap-3 text-ink-gray-8" id="enrolled-count"><span class="lucide-users size-4 shrink-0"></span><span>12 enrolled</span></div>
		<div class="flex items-center gap-3 text-ink-gray-8" id="lessons"><span class="lucide-book-open size-4 shrink-0"></span><span>5 lessons</span></div>
		</section></div></div></aside></div></div>`;

const CONTINUE = `<div class="space-y-2 mb-8"><a href="/lms/courses/paid-course/learn/1-3"><button>Continue Learning</button></a></div>`;
const BUY = `<a href="/lms/billing/course/paid-course"><button>Buy this course</button></a>`;
const NO_REVIEWS = `<div class="mt-12" id="reviews"><div class="flex items-center justify-between gap-3 mb-8" id="reviews-head">
	<div class="flex items-center gap-2" id="rating"><svg class="size-5 fill-yellow-500"></svg><span>0</span><span>course rating &amp; 0 user ratings</span></div>
	<button id="write-review">Write a Review</button></div></div>`;
const REVIEWS = `<div class="mt-12" id="reviews"><div class="flex items-center justify-between gap-3 mb-8" id="reviews-head">
	<div class="flex items-center gap-2" id="rating"><span>4.8</span></div><button id="write-review">Write a Review</button></div>
	<div class="grid grid-cols-1 md:grid-cols-2"><article class="flex gap-4" id="review"><div class="flex items-center">
	<svg class="size-4 text-transparent fill-yellow-500" id="review-star"></svg></div>Very useful.</article></div></div>`;

const LESSON = `<div class="grid md:grid-cols-[70%,30%] sm:h-[94vh]"><div class="bg-surface-base min-w-0">
	<div class="sm:border-e pt-8 sm:pt-5 pb-10 h-full"><div class="px-5" id="column">
	<h1 class="text-4xl-semibold text-ink-gray-9" id="lesson-title">Lesson</h1>
	<div class="ProseMirror prose prose-sm max-w-none !whitespace-normal mt-8" id="text"><div id="editor"></div></div>
	</div></div></div></div>`;

// jsdom neither inherits custom properties nor resolves var(). This looks each one up on the element
// and its ancestors, as a browser would.
function resolve(win, element, value) {
	return value.replace(/var\((--[\w-]+)(?:,\s*([^)]*))?\)/g, (match, name, fallback) => {
		for (let node = element; node && node.nodeType === 1; node = node.parentElement) {
			const found = win.getComputedStyle(node).getPropertyValue(name).trim();
			if (found) return resolve(win, node, found);
		}
		return fallback || "";
	});
}

function page(attributes, body) {
	const html = Object.entries(attributes)
		.map(([key, value]) => ` ${key}="${value}"`)
		.join("");
	const dom = new JSDOM(`<!DOCTYPE html><html${html}><head><style>${CSS}</style></head><body>${body}</body></html>`);
	const win = dom.window;
	const node = (id) => win.document.getElementById(id);
	const style = (id) => win.getComputedStyle(node(id));
	// A property of an element as the browser would use it, with every var() resolved. jsdom keeps a
	// shorthand that holds var() as written, so ask for the shorthand, such as "border-radius" or "font".
	const value = (id, property) => resolve(win, node(id), style(id).getPropertyValue(property));
	// A token as the page defines it on <html>.
	const token = (name) => win.getComputedStyle(win.document.documentElement).getPropertyValue(name).trim();
	return { hidden: (id) => style(id).display === "none", style, value, token };
}

test("the stylesheet parses into rules", () => {
	const dom = new JSDOM(`<!DOCTYPE html><html><head><style>${CSS}</style></head><body></body></html>`);
	const rules = dom.window.document.styleSheets[0].cssRules;
	assert.ok(rules.length >= 12, `only ${rules.length} rules`);
	for (const rule of rules) dom.window.document.querySelectorAll(rule.selectorText);
});

test("a learner sees no streak, profile prompt or Powered by mark", () => {
	const learner = page({ "data-al-page": "home" }, SIDEBAR + HOME);
	for (const id of ["streak", "profile", "profile-collapsed", "powered"]) assert.ok(learner.hidden(id), id);
	assert.ok(!learner.hidden("help"));
	assert.ok(!learner.hidden("greeting"));
	assert.match(learner.value("greeting", "font-family"), /Instrument Serif/);
});

test("a learner who has not bought the course sees the offer, without counts or the Published badge", () => {
	const visitor = page({ "data-al-page": "course", "data-al-enrolled": "0" }, COURSE.replace("CARD_ACTION", BUY).replace("REVIEWS", ""));
	for (const id of ["published", "students", "students-dot", "enrolled-count"]) assert.ok(visitor.hidden(id), id);
	for (const id of ["price", "lessons", "instructors"]) assert.ok(!visitor.hidden(id), id);
	assert.match(visitor.value("title", "font-family"), /Instrument Serif/);
	assert.strictEqual(visitor.style("title").fontWeight, "400");
	assert.match(visitor.value("price", "font"), /Instrument Serif/);
});

test("an enrolled learner sees the course to continue, not the offer", () => {
	const learner = page({ "data-al-page": "course", "data-al-enrolled": "1" }, COURSE.replace("CARD_ACTION", CONTINUE).replace("REVIEWS", NO_REVIEWS));
	for (const id of ["published", "price", "students", "students-dot", "enrolled-count", "rating"]) {
		assert.ok(learner.hidden(id), id);
	}
	for (const id of ["lessons", "instructors", "write-review", "reviews"]) assert.ok(!learner.hidden(id), id);
});

test("without the script, an enrolled learner's card still drops the price", () => {
	const learner = page({}, COURSE.replace("CARD_ACTION", CONTINUE).replace("REVIEWS", ""));
	assert.ok(learner.hidden("price"));
	assert.ok(learner.hidden("enrolled-count"));
	assert.ok(!learner.hidden("published"));
});

test("written reviews all stay, without the average above them", () => {
	const learner = page({ "data-al-page": "course", "data-al-enrolled": "1" }, COURSE.replace("CARD_ACTION", CONTINUE).replace("REVIEWS", REVIEWS));
	assert.ok(learner.hidden("rating"));
	for (const id of ["reviews", "review", "review-star", "write-review"]) assert.ok(!learner.hidden(id), id);
	assert.strictEqual(learner.value("review", "border-radius"), "24px");
});

test("staff see the Published badge", () => {
	const staff = page({ "data-al-page": "course", "data-al-staff": "1" }, COURSE.replace("CARD_ACTION", "").replace("REVIEWS", ""));
	assert.ok(!staff.hidden("published"));
});

test("lesson text is set for reading", () => {
	const lesson = page({ "data-al-page": "lesson" }, LESSON);
	assert.strictEqual(lesson.style("text").fontSize, "1.0625rem");
	assert.strictEqual(lesson.value("column", "max-width"), "42.5rem");
	assert.match(lesson.value("lesson-title", "font-family"), /Instrument Serif/);
	assert.strictEqual(lesson.value("text", "color"), "#2b2734");
});

// A course card (CourseCard.vue) inside its link, as the catalogue and the home page show it.
function card({ progress = null, price = "$ 299" } = {}) {
	const bar =
		progress == null
			? ""
			: `<div class="w-full bg-surface-gray-3 rounded-full h-1" id="bar"><div class="bg-surface-gray-10 rounded-full h-1" style="width: ${progress}%;"></div></div>
			<div class="text-sm mt-2 mb-4" id="progress-text">${progress}% completed</div>`;
	return `<div class="grid grid-cols-1 gap-5" id="grid"><a href="/lms/courses/paid-course">
	<div class="flex flex-col h-full rounded-md overflow-auto bg-surface-elevation-1" style="min-height: 350px;" id="card">
	<div class="w-[100%] h-[168px] bg-cover border-t border-x rounded-t-md" id="banner" style="background-image: linear-gradient(to top right, black, var(--violet-400)); background-blend-mode: screen;">
	<div class="flex items-center justify-center text-white flex-1 h-full text-lg" id="banner-title">Course</div></div>
	<div class="flex flex-col flex-auto p-4 border-x-2 border-b-2 rounded-b-md" id="card-body">
	<div class="flex items-center justify-between mb-2">
		<div id="card-lessons"><span class="flex items-center"><span class="lucide-book-open size-4 me-1"></span>5</span></div>
		<div id="card-students"><span class="flex items-center"><span class="lucide-users size-4 me-1"></span>12</span></div>
		<div id="card-rating"><span class="flex items-center"><svg class="size-4 me-1 text-transparent fill-yellow-500"></svg>0</span></div>
		<span class="lucide-award size-4 text-ink-amber-6" id="card-award"></span></div>
	<div class="short-introduction text-sm" id="card-intro">For managers and leaders.</div>
	${bar}
	<div class="flex items-center justify-between mt-auto"><div class="flex avatar-group overlap" id="card-instructor">Instructor</div>
	<div class="flex items-center gap-x-2"><div class="font-semibold" id="card-price">${price}</div></div></div>
	</div></div></a></div>`;
}

const HERO_RATING = `<div class="flex items-center gap-1.5" id="hero-rating"><svg class="size-4 fill-yellow-500"></svg><span>4.0</span></div>`;

const DESCRIPTION = `<div class="ProseMirror prose prose-sm max-w-none" id="description">LIST</div>`;

const LESSON_OUTLINE = `<aside class="sticky top-10 h-[94vh]"><div class="flex flex-col h-full">
	<div class="bg-surface-gray-1 px-5 py-5 border-b" id="outline-head"><div class="text-lg-semibold text-ink-gray-9" id="outline-title">Course</div>
	<div class="flex items-center gap-2 text-sm text-ink-gray-7 mt-4" id="outline-completed"><span>Completed 0%</span></div>
	<div class="h-1 w-full rounded-full bg-surface-gray-2 overflow-hidden mt-2" id="outline-bar"><div class="h-full" style="width: 0%;"></div></div></div>
	<ul class="flex-1 overflow-y-auto px-2 py-3 list-none"><li><button type="button">Chapter</button></li></ul></div></aside>`;

const HOME_SUBTITLE = `<main id="main-content"><div class="w-full p-5"><div class="space-y-2"><div class="flex items-center justify-between">
	<h1 class="text-2xl-bold text-ink-gray-9">Hey, Learner</h1></div><div class="text-lg text-ink-gray-6 leading-6" id="subtitle">Resume where you left off</div></div></div></main>`;

test("without the script, no token changes and no rule hides anything", () => {
	const plain = page({}, SIDEBAR + HOME + COURSE.replace("CARD_ACTION", BUY).replace("REVIEWS", NO_REVIEWS) + card());
	for (const id of ["streak", "profile", "profile-collapsed", "powered", "published", "price", "students", "students-dot", "enrolled-count", "rating", "card-students", "card-rating", "card-award", "card-price"]) {
		assert.ok(!plain.hidden(id), id);
	}
	for (const name of ["--al-ink", "--ink-gray-9", "--surface-gray-2", "--outline-gray-1", "--header-frame-h"]) {
		assert.strictEqual(plain.token(name), "", name);
	}
	assert.doesNotMatch(plain.value("title", "font-family"), /Instrument Serif/);
});

test("the page takes the website's tokens in both themes", () => {
	const light = page({ "data-al-page": "home" }, HOME);
	assert.strictEqual(light.token("--al-accent"), "#5b3fd6");
	assert.strictEqual(light.token("--header-frame-h"), "3.5rem");
	const dark = page({ "data-al-page": "home", "data-theme": "dark" }, HOME);
	assert.strictEqual(dark.token("--al-accent"), "#c9b6ff");
	assert.strictEqual(dark.token("--al-bg"), "#121019");
	assert.strictEqual(dark.token("--surface-base"), "var(--al-bg)");
	assert.strictEqual(dark.token("--ink-gray-9"), "var(--al-ink)");
});

test("staff keep the learner count, the enrolled count and the average", () => {
	const staff = page(
		{ "data-al-page": "course", "data-al-staff": "1", "data-al-enrolled": "0" },
		COURSE.replace("CARD_ACTION", BUY).replace("REVIEWS", REVIEWS) + card()
	);
	for (const id of ["students", "enrolled-count", "rating", "card-students", "card-rating", "card-award"]) {
		assert.ok(!staff.hidden(id), id);
	}
});

test("a course card shows the lessons, the introduction, the instructor and the price, and no rating or counts", () => {
	const catalogue = page({ "data-al-page": "courses" }, card());
	for (const id of ["card-students", "card-rating", "card-award"]) assert.ok(catalogue.hidden(id), id);
	for (const id of ["card-lessons", "card-intro", "card-instructor", "card-price"]) assert.ok(!catalogue.hidden(id), id);
	assert.strictEqual(catalogue.value("card", "border-radius"), "24px");
	assert.match(catalogue.value("banner-title", "font"), /Instrument Serif/);
	assert.strictEqual(catalogue.value("banner-title", "align-items"), "flex-end");
});

test("a course card of the learner's own course shows progress and no price", () => {
	const home = page({ "data-al-page": "home", "data-al-courses": "1" }, card({ progress: 40 }));
	assert.ok(home.hidden("card-price"));
	for (const id of ["bar", "progress-text", "card-lessons", "card-intro"]) assert.ok(!home.hidden(id), id);
	assert.strictEqual(home.value("bar", "background"), "#efe7ff");
});

test("the hero hides the average rating and a written review keeps its stars", () => {
	const visitor = page(
		{ "data-al-page": "course", "data-al-enrolled": "0" },
		COURSE.replace('<div class="flex items-center" id="instructors">', HERO_RATING + '<div class="flex items-center" id="instructors">')
			.replace("CARD_ACTION", BUY)
			.replace("REVIEWS", REVIEWS)
	);
	assert.ok(visitor.hidden("hero-rating"));
	assert.ok(!visitor.hidden("review-star"));
	assert.ok(!visitor.hidden("review"));
});

test("the first list of the description becomes the outcome card, and no list draws none", () => {
	const course = (list) =>
		page({ "data-al-page": "course" }, COURSE.replace("CARD_ACTION", BUY).replace("REVIEWS", DESCRIPTION.replace("LIST", list)));
	const withList = course('<p id="intro">Intro.</p><ul id="outcomes"><li>One</li><li>Two</li></ul><ul id="second"><li>Three</li></ul>');
	assert.strictEqual(withList.value("outcomes", "border-radius"), "24px");
	assert.strictEqual(withList.value("outcomes", "list-style"), "none");
	assert.notStrictEqual(withList.value("second", "border-radius"), "24px");
	const withoutList = course('<p id="intro">Intro.</p><h3 id="heading">More</h3><p id="more">Text.</p>');
	for (const id of ["intro", "heading", "more"]) {
		assert.notStrictEqual(withoutList.value(id, "border-radius"), "24px", id);
		assert.strictEqual(withoutList.value(id, "background"), "", id);
	}
});

test("the lesson outline drops Completed 0% for a learner who has not bought a paid course", () => {
	const visitor = page({ "data-al-page": "lesson", "data-al-paid": "1", "data-al-enrolled": "0" }, LESSON_OUTLINE);
	assert.ok(visitor.hidden("outline-completed"));
	assert.ok(visitor.hidden("outline-bar"));
	assert.ok(!visitor.hidden("outline-title"));
	const learner = page({ "data-al-page": "lesson", "data-al-paid": "1", "data-al-enrolled": "1" }, LESSON_OUTLINE);
	for (const id of ["outline-completed", "outline-bar", "outline-title"]) assert.ok(!learner.hidden(id), id);
	// A free course keeps its progress: "Enroll Now" does not reload the page, so the mark can be out of date.
	const free = page({ "data-al-page": "lesson", "data-al-enrolled": "0" }, LESSON_OUTLINE);
	for (const id of ["outline-completed", "outline-bar", "outline-title"]) assert.ok(!free.hidden(id), id);
});

test("the home subtitle shows only to a learner with a course", () => {
	assert.ok(page({ "data-al-page": "home", "data-al-courses": "0" }, HOME_SUBTITLE).hidden("subtitle"));
	assert.ok(!page({ "data-al-page": "home", "data-al-courses": "1" }, HOME_SUBTITLE).hidden("subtitle"));
	assert.ok(!page({ "data-al-page": "home", "data-al-courses": "0", "data-al-staff": "1" }, HOME_SUBTITLE).hidden("subtitle"));
});
