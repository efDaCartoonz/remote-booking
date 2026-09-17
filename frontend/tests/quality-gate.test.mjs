import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";

const packageUrl = new URL("../package.json", import.meta.url);

test("frontend exposes a native test command for the quality gate", async () => {
  const packageJson = JSON.parse(await readFile(packageUrl, "utf8"));

  assert.equal(packageJson.scripts.test, "node --test tests/*.test.mjs");
  assert.match(packageJson.scripts.build, /vue-tsc/);
});
