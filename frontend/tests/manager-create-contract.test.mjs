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
