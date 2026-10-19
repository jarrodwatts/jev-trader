import { appendFileSync, mkdirSync, renameSync, rmSync, statSync } from "node:fs";
import { dirname } from "node:path";
import { config } from "./config";

/**
 * Append-only archive of every block event at data/events.jsonl. The in-memory history
 * (`historySize` events, served by /history) is the supported history interface; the file is a
 * bounded local archive for post-run analysis. When an append would pass `eventLogMaxBytes`
 * the file rotates to `${path}.1` (replacing the previous rotation), so disk use stays at
 * twice the cap regardless of how long the bot runs. Disabled entirely by `EVENTS_LOG=false`.
 */
export class EventLog {
  private bytes = 0;
  private readonly path: string;

  constructor(path = "data/events.jsonl") {
    this.path = path;
    if (!config.eventLogEnabled) return;
    mkdirSync(dirname(path), { recursive: true });
    this.bytes = statSync(path, { throwIfNoEntry: false })?.size ?? 0;
  }

  /** Append one serialized event line, rotating first when that would pass the cap. */
  append(line: string) {
    if (!config.eventLogEnabled) return;
    const size = Buffer.byteLength(line) + 1; // the newline appendFileSync adds
    if (this.bytes + size > config.eventLogMaxBytes) this.rotate();
    this.bytes += size;
    appendFileSync(this.path, line + "\n");
  }

  /** POSIX rename replaces an existing destination; clear the old backup first for the same result everywhere. */
  private rotate() {
    try {
      if (this.bytes > 0) {
        rmSync(this.path + ".1", { force: true });
        renameSync(this.path, this.path + ".1");
      }
      this.bytes = 0;
    } catch (e) {
      // Keep appending and try again on the next block: rotation is best effort, the cap approximate.
      console.error("events.jsonl rotate:", (e as Error).message);
    }
  }
}
