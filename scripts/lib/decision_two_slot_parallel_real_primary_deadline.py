"""Prospective worker deadline with two actual primary slots and raw admission proof."""
import sys
from scripts.lib import decision_two_slot_real_primary_deadline as deadline
from scripts.lib import decision_two_slot_real_primary_cancellation as primary
from scripts.lib import decision_two_slot_parallel_real_primary_cancellation as parallel

PLAN_SCHEMA = 'agat.decision.two-slot-parallel-real-primary-deadline-plan.v1'
RESULT_SCHEMA = 'agat.decision.two-slot-parallel-real-primary-deadline-result.v1'
VERIFICATION_SCHEMA = 'agat.decision.two-slot-parallel-real-primary-deadline-verification.v1'
REAL_PRIMARY = PRIMARY_RUNNER_PROGRESS = True
real = primary.real
DRIVER_PATH = 'scripts/run-two-slot-parallel-real-primary-deadline.mts'
SOURCE_PATHS = [*deadline.SOURCE_PATHS, 'scripts/run-two-slot-parallel-real-primary-deadline.py', DRIVER_PATH,
    'scripts/verify-two-slot-parallel-real-primary-deadline.py', 'scripts/test/test_decision_two_slot_parallel_real_primary_deadline.py']
ARTIFACTS = deadline.ARTIFACTS | {'primary-runner.json', 'primary-progress.jsonl', 'primary-parallel-progress.jsonl'}
AUTHORITY = deadline.AUTHORITY
PREPARED_FILE, READY_FILE, DRAINED_FILE = deadline.PREPARED_FILE, deadline.READY_FILE, deadline.DRAINED_FILE
GENERATION, SETTINGS, WARMUP_REQUEST = parallel.GENERATION, parallel.SETTINGS, parallel.WARMUP_REQUEST
OwnedPrimary, prepare_primary = parallel.OwnedPrimary, parallel.prepare_primary
projection, make_proxy, native_fault = deadline.projection, deadline.make_proxy, deadline.native_fault
raw_body, journal, verify_physical, recovery = deadline.raw_body, deadline.journal, deadline.verify_physical, deadline.recovery
shared_config, agent_stage, validate_preparation = deadline.shared_config, deadline.agent_stage, deadline.validate_preparation
verify_primary_response, verify_primary_inventory = parallel.verify_primary_response, parallel.verify_primary_inventory


def protocol(context, index):
    return {**parallel.protocol(context, index), 'kind': 'worker_deadline_while_peer_primary_in_flight',
        'startOrder': [0, 1, 2, 3], 'caseVersions': [1, 2, 1, 1], 'targetCallerTimeoutMs': 250, 'healthyCallerTimeoutMs': 10000,
        'nativeFault': native_fault(projection(context, index), 1)}


def verify_actor(context, spec, recipe, driver, artifacts):
    evidence = deadline.common.verify_actor(context, spec, recipe, driver, artifacts, suite=sys.modules[__name__])
    return {**evidence, **primary.verify_primary_peer(context, spec, recipe, artifacts, ready_file=READY_FILE, parallel=2),
        **parallel.verify_progress(context, spec, recipe, artifacts)}


def inventory(context, plan, result, artifacts):
    return deadline.common.inventory(context, plan, result, artifacts, suite=sys.modules[__name__])


def verify(root, directory, context_path, **kwargs):
    return deadline.transport.verify(root, directory, context_path, suite=sys.modules[__name__], **kwargs)
