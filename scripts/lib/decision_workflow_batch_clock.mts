/** Retain the monotonic interval enclosing each actual wall-clock read. */
export const BATCH_CLOCK_SCHEMA = "agat.decision.paired-workflow-batch.v2";

export type BatchClockSample = {
  wallAt: string;
  monotonicBeforeMs: number;
  monotonicAfterMs: number;
};

export function sampleBatchClock(
  monotonic: () => number = () => performance.now(),
  wall: () => number = () => Date.now(),
): BatchClockSample {
  const monotonicBeforeMs = monotonic();
  const wallMs = wall();
  const monotonicAfterMs = monotonic();
  return { wallAt: new Date(wallMs).toISOString(), monotonicBeforeMs, monotonicAfterMs };
}

export function batchElapsedMs(start: BatchClockSample, end: BatchClockSample): number {
  return Math.round((end.monotonicBeforeMs - start.monotonicAfterMs) * 1000) / 1000;
}
