import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('resident_prepare',ROOT/'scripts/prepare-decision-resident-deployment.py')
prepare = importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(prepare)


class ResidentDeploymentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def wheel(self,name='mlx',version='0.32.2',filename=None):
        path = self.root/(filename or f'{name}-{version}-py3-none-any.whl')
        with zipfile.ZipFile(path,'x') as archive:
            archive.writestr(f'{name}-{version}.dist-info/METADATA',f'Name: {name}\nVersion: {version}\n')
        return path

    def test_dependency_pins_are_exact_normalized_and_unique(self):
        path = self.root/'requirements.txt';path.write_text('mlx==0.32.2\ntyping_extensions==4.16.0\n')
        self.assertEqual(prepare.requirements(path),{'mlx':'0.32.2','typing-extensions':'4.16.0'})
        for invalid in ('mlx>=0.32.2\n','mlx==0.32.2\nmlx==0.32.2\n','typing_extensions==4.16.0\ntyping-extensions==4.16.0\n',''):
            with self.subTest(invalid=invalid):
                path.write_text(invalid)
                with self.assertRaises(ValueError): prepare.requirements(path)

    def test_wheelhouse_binds_metadata_to_versions_and_hash_lock(self):
        self.wheel();wheels,lock = prepare.wheel_lock(self.root,{'mlx':'0.32.2'})
        self.assertEqual(lock,f'mlx==0.32.2 --hash=sha256:{wheels["mlx"]["sha256"]}\n')
        self.assertEqual(len(wheels['mlx']['sha256']),64)

    def test_wrong_foreign_duplicate_or_missing_wheels_are_rejected(self):
        self.wheel()
        for pins in ({'mlx':'0.32.1'},{'numpy':'2.5.3'},{'mlx':'0.32.2','numpy':'2.5.3'}):
            with self.subTest(pins=pins),self.assertRaises(ValueError): prepare.wheel_lock(self.root,pins)
        self.wheel(filename='duplicate-0-py3-none-any.whl')
        with self.assertRaises(ValueError): prepare.wheel_lock(self.root,{'mlx':'0.32.2'})

    def test_cloud_only_wheel_is_rejected_before_opening_it(self):
        path = self.wheel()
        with patch.object(prepare.stat,'SF_DATALESS',0x40000000,create=True),patch.object(Path,'stat',return_value=SimpleNamespace(st_flags=0x40000000)),patch.object(Path,'is_file',return_value=True),patch.object(prepare.zipfile,'ZipFile') as archive:
            with self.assertRaisesRegex(ValueError,'Cloud-only'): prepare.wheel_lock(self.root,{'mlx':'0.32.2'})
            archive.assert_not_called()

    def test_destination_refuses_existing_symlink_foreign_or_protected_paths(self):
        base = self.root/'Library/Application Support/Agat/decision-shadow/releases';base.mkdir(parents=True)
        new = base/'new-release';self.assertEqual(prepare.new_destination(new,self.root),new)
        new.mkdir()
        with self.assertRaises(ValueError): prepare.new_destination(new,self.root)
        linked = base/'link';linked.symlink_to(self.root/'missing')
        for path in (linked,self.root/'Documents/release',self.root/'arbitrary',base/'subdir/release'):
            with self.subTest(path=path),self.assertRaises(ValueError): prepare.new_destination(path,self.root)

    def test_cloud_only_model_is_rejected_before_manifest_read_or_hash(self):
        manifest = self.root/'manifest.json';manifest.write_text('{}')
        with patch.object(prepare.stat,'SF_DATALESS',0x40000000,create=True),patch.object(Path,'stat',return_value=SimpleNamespace(st_flags=0x40000000)),patch.object(prepare,'read_json') as reader,patch.object(prepare,'verify_manifest') as verify:
            with self.assertRaisesRegex(ValueError,'cloud-only'): prepare.resident_manifest(manifest)
            reader.assert_not_called();verify.assert_not_called()

    def test_model_traversal_is_rejected_before_model_verification(self):
        manifest = self.root/'manifest.json';manifest.write_text(json.dumps({'snapshot':str(self.root),'files':{'../outside':'a'*64}}))
        with patch.object(prepare,'verify_manifest') as verify:
            with self.assertRaisesRegex(ValueError,'filename'): prepare.resident_manifest(manifest)
            verify.assert_not_called()

    def test_scraper_config_is_foreground_loopback_and_owned_storage(self):
        config = prepare.prometheus_agent(self.root,self.root/'bin/prometheus',9095)
        self.assertEqual(config['Label'],'org.agat.decision-prometheus')
        self.assertIn('--web.listen-address=127.0.0.1:9095',config['ProgramArguments'])
        self.assertEqual(config['KeepAlive'],{'SuccessfulExit':False})
        self.assertEqual(config['ThrottleInterval'],30)
        for port in (True,80,65536):
            with self.subTest(port=port),self.assertRaises(ValueError): prepare.prometheus_agent(self.root,self.root/'bin/prometheus',port)


if __name__ == '__main__': unittest.main()
