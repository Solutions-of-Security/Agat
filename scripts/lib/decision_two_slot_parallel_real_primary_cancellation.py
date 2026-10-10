"""Prospective native cancellation with two worker and two actual primary slots."""
import sys
from scripts.lib import decision_two_slot_cancellation as common
from scripts.lib import decision_two_slot_real_primary_cancellation as primary

PLAN_SCHEMA = 'agat.decision.two-slot-parallel-real-primary-cancellation-plan.v1'
RESULT_SCHEMA = 'agat.decision.two-slot-parallel-real-primary-cancellation-result.v1'
VERIFICATION_SCHEMA = 'agat.decision.two-slot-parallel-real-primary-cancellation-verification.v1'
REAL_PRIMARY = True
real = primary.real
DRIVER_PATH = 'scripts/run-two-slot-parallel-real-primary-cancellation.mts'
SOURCE_PATHS = [*primary.SOURCE_PATHS, 'scripts/run-two-slot-parallel-real-primary-cancellation.py', DRIVER_PATH,
    'scripts/verify-two-slot-parallel-real-primary-cancellation.py', 'scripts/test/test_decision_two_slot_parallel_real_primary_cancellation.py']
ARTIFACTS, AUTHORITY = primary.ARTIFACTS, primary.AUTHORITY
PREPARED_FILE, READY_FILE, DRAINED_FILE = primary.PREPARED_FILE, primary.READY_FILE, primary.DRAINED_FILE
GENERATION, SETTINGS, WARMUP_REQUEST = real.GENERATION, real.SETTINGS, real.WARMUP_REQUEST
OwnedPrimary, prepare_primary = real.OwnedPrimary, real.prepare
projection, selected = primary.projection, primary.selected
make_proxy, recovery, verify_physical = primary.make_proxy, primary.recovery, primary.verify_physical
journal, raw_body, shared_config = primary.journal, primary.raw_body, primary.shared_config
agent_stage, validate_preparation = primary.agent_stage, primary.validate_preparation
verify_primary_response = primary.verify_primary_response


def protocol(context, target): return {**primary.protocol(context, target), 'primaryNumParallel': 2}


def verify_actor(context, spec, recipe, driver, artifacts):
    evidence = common.verify_actor(context, spec, recipe, driver, artifacts, suite=sys.modules[__name__])
    return {**evidence, **primary.verify_primary_peer(context, spec, recipe, artifacts, parallel=2)}


def verify_primary_inventory(plan, result, recipe, artifacts):
    return primary.verify_primary_inventory(plan, result, recipe, artifacts, settings=SETTINGS, parallel=2)


def inventory(context, plan, result, artifacts): return common.inventory(context, plan, result, artifacts, suite=sys.modules[__name__])


def verify(root, directory, context_path, **pins): return common.verify(root, directory, context_path, suite=sys.modules[__name__], **pins)
