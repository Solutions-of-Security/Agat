"""Check raw clock brackets without assuming sequential clock reads are simultaneous."""
from decision_runtime.contracts import fields, number
from scripts.lib.decision_shadow_pilot import require, timestamp

BATCH_CLOCK_SCHEMA = 'agat.decision.paired-workflow-batch.v2'
WALL_RESOLUTION_ALLOWANCE_MS = 2


def verify_batch_clock(batch, previous_end=None):
    fields(batch, {'schemaVersion', 'index', 'inputIndices', 'runIds', 'startedAt', 'completedAt',
                   'elapsedMs', 'clockSamples'})
    require(batch['schemaVersion'] == BATCH_CLOCK_SCHEMA, 'Unknown paired batch clock schema')
    samples = fields(batch['clockSamples'], {'start', 'end'})
    values = []
    for boundary, wall_key in (('start', 'startedAt'), ('end', 'completedAt')):
        sample = fields(samples[boundary], {'wallAt', 'monotonicBeforeMs', 'monotonicAfterMs'})
        require(sample['wallAt'] == batch[wall_key], 'Batch timestamp differs from actual clock sample')
        before = number(sample['monotonicBeforeMs'], 0, 600000)
        after = number(sample['monotonicAfterMs'], 0, 600000)
        require(before <= after, 'Clock sampling bracket runs backwards')
        values.append((before, after))
    (start_before, start_after), (end_before, end_after) = values
    require(start_after <= end_before and (previous_end is None or previous_end <= start_before),
            'Paired batches overlap or monotonic clock runs backwards')
    lower = end_before - start_after
    upper = end_after - start_before
    require(upper <= 20000, 'Pair including clock sampling crossed completion deadline')
    elapsed = number(batch['elapsedMs'], 0, 20000)
    require(abs(elapsed-lower) <= .000501, 'Batch elapsed time differs from recorded monotonic boundaries')
    wall = (timestamp(batch['completedAt'], 'batch.completedAt') -
            timestamp(batch['startedAt'], 'batch.startedAt')).total_seconds() * 1000
    require(lower-WALL_RESOLUTION_ALLOWANCE_MS <= wall <= upper+WALL_RESOLUTION_ALLOWANCE_MS,
            'Wall clock differs from recorded monotonic interval; correlation is unverified')
    return end_after, {'samplingMs': [start_after-start_before, end_after-end_before],
                       'wallDurationMs': wall, 'monotonicLowerMs': lower, 'monotonicUpperMs': upper}
