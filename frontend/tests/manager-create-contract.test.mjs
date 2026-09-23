import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";

const appUrl = new URL("../src/App.vue", import.meta.url);

test("manager create browser contract contains only the public ticket number", async () => {
  const source = await readFile(appUrl, "utf8");

  assert.doesNotMatch(source, /create\.caseId/);
  assert.doesNotMatch(source, /case_id/);
  assert.match(
    source,
    /\/api\/v1\/manager\/tickets\/\$\{encodeURIComponent\(create\.value\.caseNumber\)\}\/preflight/,
  );
  assert.match(source, /Номер тикета/);
});

test("manager summary types and renders urgent and urgent_collision while preserving case_number privacy", async () => {
  const source = await readFile(appUrl, "utf8");

  assert.match(
    source,
    /summary:\s*\{\s*assigned:\s*number;\s*confirmed:\s*number;\s*rejected:\s*number;\s*overdue:\s*number;\s*urgent:\s*number;\s*urgent_collision:\s*number\s*\}/,
  );
  assert.match(source, /manager\.summary\.urgent\b/);
  assert.match(source, /manager\.summary\.urgent_collision\b/);
  assert.match(
    source,
    /<strong>\{\{\s*manager\.summary\.urgent\s*\}\}<\/strong><span>Срочно<\/span>/,
  );
  assert.match(
    source,
    /<strong>\{\{\s*manager\.summary\.urgent_collision\s*\}\}<\/strong><span>Коллизии<\/span>/,
  );
  assert.doesNotMatch(source, /case_id/);
});
