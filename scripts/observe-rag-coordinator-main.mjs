/** Qualification-only observer, loaded before the unmodified compiled main. */
import assert from 'node:assert/strict';
import { monitorEventLoopDelay } from 'node:perf_hooks';
import { AgatStore } from '../apps/coordinator/dist/database.js';

assert.equal(process.env.AGAT_RAG_HTTP_DISPOSABLE, '1');
assert.equal(process.env.AGAT_STATE_STORE_DRIVER, 'postgresql');
assert.ok(process.send, 'The observer requires its parent IPC channel');
for (const key of ['AGAT_POSTGRES_URL', 'AGAT_POSTGRES_TENANT_URL']) {
  const url = new URL(process.env[key]);
  assert.equal(url.hostname, '127.0.0.1'); assert.equal(url.pathname, '/agat_rag_http');
}
const round = value => Math.round(value * 1000) / 1000;
const lag = monitorEventLoopDelay({ resolution: 10 });
let phase;
const maintenanceTick = AgatStore.prototype.maintenanceTick;
AgatStore.prototype.maintenanceTick = function (...args) {
  const started = performance.now();
  let error = null;
  try { return maintenanceTick.apply(this, args); }
  catch (failure) { error = failure instanceof Error ? failure.name : 'MaintenanceError'; throw failure; }
  finally { phase?.maintenance.push({ durationMs: round(performance.now() - started), error }); }
};
process.on('message', command => {
  assert.match(command.id, /^(warmup|idle|c[124]-r[123])$/);
  if (command.type === 'probe-start') {
    assert.equal(phase, undefined);
    phase = { id: command.id, maintenance: [] };
    lag.reset(); lag.enable();
    process.send({ type: 'probe-started', id: command.id });
  } else if (command.type === 'probe-finish') {
    assert.ok(phase); assert.equal(phase.id, command.id); lag.disable();
    process.send({ type: 'probe-finished', ...phase,
      eventLoop: { maxMs: round(lag.max / 1e6), p99Ms: round(lag.percentile(99) / 1e6), samples: lag.count },
      memory: process.memoryUsage(), processLifetimePeakRssBytes: process.resourceUsage().maxRSS * 1024 });
    phase = undefined;
  } else throw new Error('Unknown main observer command');
});
