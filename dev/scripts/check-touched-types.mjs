#!/usr/bin/env node
/**
 * No new TypeScript errors in the files this change owns.
 *
 * The frontend has no type checker installed and CI never runs one, so a whole-
 * repo "zero errors" gate is not available: vue-tsc reports a few hundred
 * pre-existing errors (every API client lacks `override`, and `new XAPI(requests)`
 * in client-user.ts fails TS2554). Asserting zero everywhere would fail on work
 * nobody here touched, and asserting "no more than before" would need a baseline
 * that rots.
 *
 * So this checks the only thing that is both meaningful and stable: the files
 * this change adds or edits must contribute no errors at all. It prints the
 * repo-wide total as context, and fails only on ours.
 *
 * Run: node dev/scripts/check-touched-types.mjs
 */

import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const FRONTEND = path.join(REPO, "frontend");

// Files this change creates. Nothing pre-exists in them, so the bar is zero.
const CREATED = [
  "app/components/Domain/Recipe/RecipeCookTimeBadge.vue",
  "app/components/Domain/Recipe/RecipeCookTimeFilter.vue",
  "app/composables/recipes/use-cook-time.ts",
  "app/components/Domain/Recipe/__tests__/RecipeCookTimeBadge.test.ts",
  "app/components/Domain/Recipe/__tests__/RecipeCookTimeFilter.test.ts",
];

// Files this change edits, which already had errors before it. The bar here is
// "nothing new", so each error is matched by file + code + message with the line
// number dropped -- adding a line shifts every error below it, and a gate that
// failed on that would be measuring the diff's length, not its quality.
const EDITED = [
  "app/components/Domain/Recipe/RecipeCard.vue",
  "app/components/Domain/Recipe/RecipeCardMobile.vue",
  "app/components/Domain/Recipe/RecipeCardSection.vue",
];

/**
 * The errors those files carried before this change, one entry per distinct
 * (file, code, message).
 *
 * Both of these are `:image="recipe.image!"`: `Recipe.image` is `unknown` in the
 * generated API types, so the non-null assertion produces `{}` where a string is
 * wanted. Present on `main`, untouched here, and not this change's to fix --
 * `frontend/app/lib/api/types/` is generated.
 */
const PRE_EXISTING = [
  {
    file: "app/components/Domain/Recipe/RecipeCardSection.vue",
    code: "TS2322",
    message: "Type '{}' is not assignable to type 'string'.",
  },
];

const TOUCHED = [...CREATED, ...EDITED];

function fail(message) {
  console.log(`TOUCHED-TYPES-FAILED: ${message}`);
  process.exit(1);
}

function run(command, args, options = {}) {
  try {
    return execFileSync(command, args, {
      cwd: FRONTEND,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
      maxBuffer: 64 * 1024 * 1024,
      ...options,
    });
  }
  catch (error) {
    // vue-tsc exits non-zero whenever it reports anything, which is the normal
    // case here; the output is still what we need to read.
    if (error.stdout !== undefined) {
      return `${error.stdout}${error.stderr ?? ""}`;
    }
    throw error;
  }
}

for (const file of TOUCHED) {
  if (!existsSync(path.join(FRONTEND, file))) {
    fail(`${file} is listed as touched but does not exist; the list has drifted from the change`);
  }
}

// The generated tsconfig vue-tsc needs.
run("pnpm", ["exec", "nuxi", "prepare"]);

const typescriptVersion = run("node", ["-p", "require('typescript/package.json').version"]).trim();
// A dlx-pulled typescript 6 or 5.3 crashes vue-tsc, so pin it to the one the
// project already resolves.
const output = run("pnpm", [
  "dlx",
  "--package",
  `typescript@${typescriptVersion}`,
  "--package",
  "vue-tsc@3",
  "vue-tsc",
  "--noEmit",
  "-p",
  ".nuxt/tsconfig.json",
]);

const errorLines = output
  .split("\n")
  .filter(line => /\berror TS\d+:/.test(line));

console.log(`vue-tsc (typescript ${typescriptVersion}): ${errorLines.length} error line(s) repo-wide`);
console.log(`checked ${CREATED.length} new and ${EDITED.length} edited file(s)`);

/** "path(12,34): error TS2322: message" -> { file, code, message }, line dropped. */
function parse(line) {
  const match = line.match(/^(.*?)\(\d+,\d+\): error (TS\d+): (.*)$/);
  if (!match) {
    return null;
  }
  return { file: match[1].trim(), code: match[2], message: match[3].trim() };
}

const parsed = errorLines.map(parse).filter(Boolean);

const inCreated = parsed.filter(e => CREATED.some(file => e.file.endsWith(file)));
if (inCreated.length) {
  for (const e of inCreated) {
    console.log(`  ${e.file}: ${e.code}: ${e.message}`);
  }
  fail(`${inCreated.length} error(s) in files this change adds, where the bar is zero`);
}

const inEdited = parsed.filter(e => EDITED.some(file => e.file.endsWith(file)));
const unexplained = inEdited.filter(
  e => !PRE_EXISTING.some(known => e.file.endsWith(known.file) && e.code === known.code && e.message === known.message),
);
if (unexplained.length) {
  for (const e of unexplained) {
    console.log(`  ${e.file}: ${e.code}: ${e.message}`);
  }
  fail(`${unexplained.length} error(s) in edited files that were not there before`);
}

// The control on the allowlist: if those errors have been fixed upstream, or the
// files stopped being checked, the entries are stale and quietly permitting
// nothing. Say so rather than carrying dead exceptions forward.
for (const known of PRE_EXISTING) {
  if (!inEdited.some(e => e.file.endsWith(known.file) && e.code === known.code && e.message === known.message)) {
    fail(`the recorded pre-existing error ${known.code} in ${known.file} no longer occurs; remove it from PRE_EXISTING`);
  }
}

console.log(`${inEdited.length} pre-existing error(s) in edited files, all accounted for`);

// A run that produced no error lines at all would pass the assertion above
// without having checked anything -- a crashed binary, a bad tsconfig path.
// The repo's known pre-existing errors are the control that it really ran.
if (errorLines.length === 0) {
  fail("vue-tsc reported no errors anywhere, including the known pre-existing ones; it probably did not run");
}

console.log("TOUCHED-TYPES-OK");
