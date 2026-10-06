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
	<div class="grid grid-cols-1 md:grid-cols-2"><article class="flex gap-4" id="review">Very useful.</article></div></div>`;

const LESSON = `<div class="grid md:grid-cols-[70%,30%] sm:h-[94vh]"><div class="bg-surface-base min-w-0">
	<div class="sm:border-e pt-8 sm:pt-5 pb-10 h-full"><div class="px-5" id="column">
	<h1 class="text-4xl-semibold text-ink-gray-9" id="lesson-title">Lesson</h1>
	<div class="ProseMirror prose prose-sm max-w-none !whitespace-normal mt-8" id="text"><div id="editor"></div></div>
	</div></div></div></div>`;

function page(attributes, body) {
	const html = Object.entries(attributes)
		.map(([key, value]) => ` ${key}="${value}"`)
		.join("");
	const dom = new JSDOM(`<!DOCTYPE html><html${html}><head><style>${CSS}</style></head><body>${body}</body></html>`);
	const style = (id) => dom.window.getComputedStyle(dom.window.document.getElementById(id));
	return { hidden: (id) => style(id).display === "none", style };
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
	assert.match(learner.style("greeting").fontFamily, /Instrument Serif/);
});

test("a learner who has not bought the course sees the offer, without the Published badge", () => {
	const visitor = page({ "data-al-page": "course", "data-al-enrolled": "0" }, COURSE.replace("CARD_ACTION", BUY).replace("REVIEWS", ""));
	assert.ok(visitor.hidden("published"));
	for (const id of ["price", "students", "students-dot", "enrolled-count", "lessons", "instructors"]) {
		assert.ok(!visitor.hidden(id), id);
	}
	assert.match(visitor.style("title").fontFamily, /Instrument Serif/);
	assert.strictEqual(visitor.style("title").fontWeight, "400");
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

test("written reviews all stay", () => {
	const learner = page({ "data-al-page": "course", "data-al-enrolled": "1" }, COURSE.replace("CARD_ACTION", CONTINUE).replace("REVIEWS", REVIEWS));
	for (const id of ["reviews", "rating", "review", "write-review"]) assert.ok(!learner.hidden(id), id);
});

test("staff see the Published badge", () => {
	const staff = page({ "data-al-page": "course", "data-al-staff": "1" }, COURSE.replace("CARD_ACTION", "").replace("REVIEWS", ""));
	assert.ok(!staff.hidden("published"));
});

test("lesson text is set for reading", () => {
	const lesson = page({ "data-al-page": "lesson" }, LESSON);
	assert.strictEqual(lesson.style("text").fontSize, "1.0625rem");
	assert.strictEqual(lesson.style("column").maxWidth, "46rem");
	assert.match(lesson.style("lesson-title").fontFamily, /Instrument Serif/);
});
