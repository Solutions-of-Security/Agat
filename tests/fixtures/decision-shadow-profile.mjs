// Test-only profile. The literal 1.0 and whitespace exercise byte-exact round trips.
export const decisionProfileJson = `{
  "schemaVersion": "agat.decision.v1", "runtimeVersion": "test-only",
  "inputFingerprintVersions": ["python-json-v1", "binary64-v1"],
  "model": {
    "repository": "test-only/browser-fixture", "revision": "fixture",
    "artifactSha256": "${"1".repeat(64)}", "tokenizerSha256": "${"2".repeat(64)}",
    "implementationSha256": "${"3".repeat(64)}", "promptVersion": "test-v1",
    "backend": "fixture", "quantization": "none"
  },
  "policy": {"id": "test-only", "minProbability": 0.8, "minMargin": 0.1, "sha256": "${"4".repeat(64)}"},
  "calibration": {"status": "uncalibrated", "temperature": 1.0, "semantics": "softmax_over_allowed_options"}
}`;
