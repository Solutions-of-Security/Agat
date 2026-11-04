"""Cloud placeholders must be rejected before hashing or launching models."""
import json
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_temporal_cleanup import launcher
import test_temporal_log_retention as retention


class ModelResidencyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='agat-residency-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.snapshot = self.root / 'snapshot'
        self.snapshot.mkdir()
        self.files = ['config.json', 'tokenizer.json', 'tokenizer_config.json', 'model.safetensors']
        for name in self.files:
            (self.snapshot / name).write_bytes(b'fixture')
        self.manifest = self.root / 'manifest.json'
        self.manifest.write_text(json.dumps({'snapshot': str(self.snapshot), 'files': dict.fromkeys(self.files, 'fixture')}))

    def flags(self, flagged=None, other_flags=0):
        original = Path.stat
        def result(path, *args, **kwargs):
            value = original(path, *args, **kwargs)
            return SimpleNamespace(st_flags=0x40000000 if path == flagged else other_flags,
                                   st_mode=value.st_mode, st_size=value.st_size, st_blocks=0)
        return patch.object(Path, 'stat', result)

    def test_each_cloud_file_is_rejected_without_opening_that_file(self):
        original = Path.open
        for flagged in [self.manifest, self.snapshot, *[self.snapshot / name for name in self.files]]:
            with self.subTest(kind=flagged.name):
                def open_file(path, *args, **kwargs):
                    if path == flagged:
                        raise AssertionError('Attempted cloud hydration')
                    return original(path, *args, **kwargs)
                with patch.object(stat, 'SF_DATALESS', 0x40000000, create=True), self.flags(flagged), \
                     patch.object(Path, 'open', open_file):
                    with self.assertRaisesRegex(ValueError, 'cloud-only.*local store'):
                        launcher.require_resident_shadow_model(self.manifest)

    def test_resident_symlinked_cache_and_zero_allocated_blocks_are_allowed(self):
        target = self.root / 'weights'
        target.write_bytes(b'fixture')
        weights = self.snapshot / 'model.safetensors'
        weights.unlink(); weights.symlink_to(target)
        with self.flags(other_flags=getattr(stat, 'UF_COMPRESSED', 0x20)):
            launcher.require_resident_shadow_model(self.manifest)

    def test_platform_without_file_flags_remains_supported(self):
        original = Path.stat
        with patch.object(Path, 'stat', lambda path, *a, **kw: SimpleNamespace(st_mode=original(path, *a, **kw).st_mode)):
            launcher.require_resident_shadow_model(self.manifest)

    def test_path_traversal_is_rejected_before_looking_outside_the_snapshot(self):
        self.manifest.write_text(json.dumps({'snapshot': str(self.snapshot), 'files': {'../private': 'fixture'}}))
        with self.assertRaisesRegex(ValueError, 'Invalid shadow model manifest'):
            launcher.require_resident_shadow_model(self.manifest)

    def test_missing_file_is_an_error_instead_of_evidence_of_residency(self):
        (self.snapshot / 'model.safetensors').unlink()
        with self.assertRaises(FileNotFoundError):
            launcher.require_resident_shadow_model(self.manifest)

    def test_launcher_rejects_cloud_weights_before_checksum_and_process_admission(self):
        fixture = retention.PrivateLogRetentionTests()
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        with patch.object(launcher.sys, 'argv', ['launcher', '--evidence-dir', str(fixture.directory),
                '--shadow-python', sys.executable, '--shadow-manifest', str(self.manifest)]), \
             patch.object(launcher, 'SHADOW_SOURCES', []), \
             patch.object(stat, 'SF_DATALESS', 0x40000000, create=True), \
             self.flags(self.snapshot / 'model.safetensors'), \
             patch('decision_runtime.model_store.verify_manifest', side_effect=AssertionError('Placeholder reached hashing')) as verify:
            with self.assertRaisesRegex(ValueError, 'cloud-only.*local store'):
                launcher.main()
            verify.assert_not_called(); fixture.popen.assert_not_called()
            self.assertFalse(fixture.directory.exists())


if __name__ == '__main__':
    unittest.main()
