"""Actual worker deadline with two slots and a naturally pending queued real primary."""
import sys
from scripts.lib import decision_two_slot_deadline as common
from scripts.lib import decision_two_slot_cancellation as transport
from scripts.lib import decision_two_slot_real_primary_cancellation as primary

PLAN_SCHEMA = 'agat.decision.two-slot-real-primary-deadline-plan.v1'
RESULT_SCHEMA = 'agat.decision.two-slot-real-primary-deadline-result.v1'
VERIFICATION_SCHEMA = 'agat.decision.two-slot-real-primary-deadline-verification.v1'
REAL_PRIMARY = True
real = primary.real
DRIVER_PATH = 'scripts/run-two-slot-real-primary-deadline.mts'
SOURCE_PATHS = [*common.SOURCE_PATHS, *primary.real.primary.PRIMARY_SOURCES,
    'scripts/run-two-slot-real-primary-deadline.py', DRIVER_PATH, 'scripts/verify-two-slot-real-primary-deadline.py',
    'scripts/test/test_decision_two_slot_real_primary_deadline.py']
ARTIFACTS = common.ARTIFACTS | {'primary.log', 'primary-before.json', 'primary-after.json', 'primary-warmup.json'}
AUTHORITY = common.AUTHORITY
PREPARED_FILE, READY_FILE, DRAINED_FILE = common.PREPARED_FILE, common.READY_FILE, common.DRAINED_FILE
GENERATION, SETTINGS, WARMUP_REQUEST = primary.GENERATION, primary.SETTINGS, primary.WARMUP_REQUEST
OwnedPrimary, prepare_primary = primary.OwnedPrimary, primary.prepare_primary
projection = primary.projection
make_proxy, native_fault = common.make_proxy, common.native_fault
raw_body, journal, verify_physical, recovery = common.raw_body, common.journal, common.verify_physical, common.recovery
shared_config = common.shared_config
verify_primary_response, verify_primary_inventory = primary.verify_primary_response, primary.verify_primary_inventory


def protocol(context, index):
    return {**primary.protocol(context, index), 'kind': 'worker_deadline_while_peer_primary_in_flight',
        'startOrder': [0, 1, 2, 3], 'caseVersions': [1, 2, 1, 1], 'targetCallerTimeoutMs': 250, 'healthyCallerTimeoutMs': 10000,
        'nativeFault': native_fault(projection(context, index), 1)}


def agent_stage(trace, case): return transport.agent_stage(trace, case, primary.real.PROTOCOL['processName'])


def validate_preparation(projected, prepared, artifacts):
    return common.validate_preparation(projected, prepared, artifacts, process_name=primary.real.PROTOCOL['processName'])


def verify_actor(context, spec, recipe, driver, artifacts):
    evidence = common.verify_actor(context, spec, recipe, driver, artifacts, suite=sys.modules[__name__])
    return {**evidence, **primary.verify_primary_peer(context, spec, recipe, artifacts, ready_file=READY_FILE)}


def inventory(context, plan, result, artifacts): return common.inventory(context, plan, result, artifacts, suite=sys.modules[__name__])


def verify(root, directory, context_path, **kwargs): return transport.verify(root, directory, context_path, suite=sys.modules[__name__], **kwargs)
