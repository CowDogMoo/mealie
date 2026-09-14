#!/usr/bin/env node
/**
 * Every cook-time translation key a component asks for exists, and every one
 * this change adds is asked for.
 *
 * Both directions matter. A missing key renders the raw key string to the user.
 * An unused key is worse than dead code here: `en-US.json` is the source Crowdin
 * translates from, so a string nobody renders becomes work in thirty languages.
 *
 * Run: node dev/scripts/check-i18n-keys.mjs
 */

import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const FRONTEND = path.join(REPO, "frontend");
const MESSAGES = path.join(FRONTEND, "app/lang/messages/en-US.json");
const PREFIX = "cook-time";

function fail(message) {
  console.log(`I18N-FAILED: ${message}`);
  process.exit(1);
}

const messages = JSON.parse(readFileSync(MESSAGES, "utf8"));
const declared = Object.keys(messages.recipe ?? {}).filter(key => key.startsWith(PREFIX));

if (!declared.length) {
  fail(`no keys starting with "${PREFIX}" in en-US.json; this check would pass vacuously`);
}

/**
 * Every .vue and .ts under app/, walked from disk rather than listed from git:
 * the components this check exists for are new, and `git ls-files` would not
 * see them until they are committed -- which would make this pass by finding
 * nothing.
 */
function sourceFiles(dir) {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "lang" || entry.name === "node_modules") {
        continue;
      }
      out.push(...sourceFiles(full));
    }
    else if (/\.(vue|ts)$/.test(entry.name)) {
      out.push(full);
    }
  }
  return out;
}

const sources = sourceFiles(path.join(FRONTEND, "app"));

const referenced = new Set();
for (const file of sources) {
  const text = readFileSync(file, "utf8");
  for (const match of text.matchAll(/recipe\.(cook-time-[a-z0-9-]+)/g)) {
    // A trailing hyphen means the match stopped at a `${...}`: this is the stem
    // of a template literal, not a key. The block below expands those.
    if (match[1].endsWith("-")) {
      continue;
    }
    referenced.add(match[1]);
  }
  // `cook-time-filter-${choice}` style templates: record the stem plus the
  // literal choices declared beside it.
  for (const match of text.matchAll(/recipe\.(cook-time-[a-z0-9-]*)\$\{/g)) {
    for (const key of declared) {
      if (key.startsWith(match[1])) {
        referenced.add(key);
      }
    }
  }
}

const unused = declared.filter(key => !referenced.has(key));
const missing = [...referenced].filter(key => !declared.includes(key));

console.log(`declared ${declared.length}, referenced ${referenced.size}`);

if (missing.length) {
  fail(`component(s) ask for key(s) that do not exist: ${missing.join(", ")}`);
}
if (unused.length) {
  fail(`key(s) nobody renders, which Crowdin would translate for nothing: ${unused.join(", ")}`);
}

console.log("I18N-OK");
