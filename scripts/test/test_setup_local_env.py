import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("setup_local_env", Path(__file__).parents[1] / "setup-local-env.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SetupLocalEnvTest(unittest.TestCase):
    def test_distinct_secrets_permissions_and_port(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            example, output = root / "example", root / ".env"
            example.write_text("AGAT_ADMIN_TOKEN=placeholder\nAGAT_SEED_DEMO=false\n")
            module.create_config(example, output, 18787, "llama3.2:latest")
            values = dict(line.split("=", 1) for line in output.read_text().splitlines())
            tokens = [values[k] for k in ("AGAT_ADMIN_TOKEN", "AGAT_ENROLLMENT_TOKEN", "AGAT_CREDENTIALS_KEY", "AGAT_SEARCH_SECRET")]
            self.assertEqual(len(set(tokens)), 4)
            self.assertTrue(all(len(token) == 64 for token in tokens))
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertEqual(values["AGAT_HTTP_PORT"], "18787")
            self.assertEqual(values["AGAT_A2A_PUBLIC_BASE_URL"], "http://127.0.0.1:18787")
            self.assertEqual(values["AGAT_SEED_DEMO"], "false")
            self.assertEqual(values["AGAT_WEB_ENABLED"], "false")
            original = output.read_text()
            with self.assertRaises(FileExistsError):
                module.create_config(example, output, 8787, "different-model")
            self.assertEqual(output.read_text(), original)

    def test_invalid_input_creates_no_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            example, output = root / "example", root / ".env"
            example.write_text("")
            for port, model in [(0, "model"), (8787, "model\nOTHER=bad")]:
                with self.assertRaises(ValueError):
                    module.create_config(example, output, port, model)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
