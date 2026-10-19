import { afterEach, expect, test } from "bun:test";
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { config } from "./config";
import { EventLog } from "./evlog";

const tmp = () => mkdtempSync(join(tmpdir(), "evlog-"));

afterEach(() => {
  config.eventLogEnabled = true;
  config.eventLogMaxBytes = 104_857_600;
});

test("appends one JSON line per event and keeps both old and new files bounded", () => {
  const dir = tmp();
  const path = join(dir, "events.jsonl");
  const log = new EventLog(path);
  log.append(JSON.stringify({ block: 1 }));
  log.append(JSON.stringify({ block: 2 }));
  const lines = readFileSync(path, "utf8").split("\n").filter((l) => l !== "");
  expect(lines).toEqual([JSON.stringify({ block: 1 }), JSON.stringify({ block: 2 })]);
  expect(existsSync(path + ".1")).toBe(false);
});

test("rotates to .1 before crossing the cap and starts a fresh file", () => {
  const dir = tmp();
  const path = join(dir, "events.jsonl");
  config.eventLogMaxBytes = 100;
  const log = new EventLog(path);
  // each append is 25 tracked bytes: the 5th would push past 100, so it rotates exactly once
  for (let i = 0; i < 5; i++) log.append(`{"i":${i}}`.padEnd(24, " "));
  expect(existsSync(path + ".1")).toBe(true);
  expect(readFileSync(path + ".1", "utf8").split("\n").filter((l) => l !== "").length).toBe(4);
  expect(readFileSync(path, "utf8").split("\n").filter((l) => l !== "").length).toBe(1);
  expect(Buffer.byteLength(readFileSync(path, "utf8"))).toBeLessThanOrEqual(25);
});

test("counts pre-existing bytes so a restart cannot blow past the cap", () => {
  const dir = tmp();
  const path = join(dir, "events.jsonl");
  config.eventLogMaxBytes = 100;
  writeFileSync(path, "y".repeat(90)); // left behind by an earlier run
  const log = new EventLog(path);
  log.append("z".repeat(24));
  expect(readFileSync(path + ".1", "utf8")).toBe("y".repeat(90));
  expect(readFileSync(path, "utf8")).toBe("z".repeat(24) + "\n");
});

test("EVENTS_LOG=false writes nothing and creates no file", () => {
  const dir = tmp();
  const path = join(dir, "events.jsonl");
  config.eventLogEnabled = false;
  const log = new EventLog(path);
  log.append(JSON.stringify({ block: 1 }));
  expect(existsSync(path)).toBe(false);
});
