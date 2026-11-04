# Structured business output: SP-002 / RUN-103, first increment

Implemented 25 October 2026. An agent node can opt into the fixed server contract
`config.outputContract: "business-report.v1"`. Its process version and running
instance retain that selection through publication, restart and BPMN round trips.
Legacy nodes keep their existing text behavior. The next increment adds the
[input contract and pinned instructions](./business-input-contracts.md).
Pack v2 remains a separate increment. Document parsing is extended in the
[tabular ingestion increment](./tabular-knowledge-ingest.md).

Validated structured output can be saved using the deterministic
[Markdown artifact format](./validated-report-markdown.md); source validation is
repeated at export rather than relying on a prior model response or human wait.

The bundled draft-07 JSON Schema is in
[`business-contracts.ts`](../apps/coordinator/src/business-contracts.ts). Each JSON
object requires `schemaVersion`, `status`, `summary`, `claims`, `calculations`,
`artifactRefs` and `limitations`. Unknown properties, implicit conversions, Markdown
fences, non-finite numbers and outputs over 200,000 UTF-8 bytes are rejected.
Arrays and individual strings have bounded sizes. Users select a supported contract
ID rather than submitting arbitrary schemas or remote references.

Claims contain `id`, `text` and nonempty `sourceRefs` such as `["K1"]`. References
must resolve to retrievals of this run and project, with matching current document
and chunk hashes. This verifies provenance availability, **not that the source
supports the claim**. Factual correctness still requires a human review.

Calculations contain `id`, `operation`, exactly two `operands`, `result` and
`sourceRefs`. The deterministic oracle supports sum, difference (`a-b`), ratio
(`a/b`), percent change (`(b-a)/abs(a)*100`) and percentage-point change
(`(b-a)*100`, with operands expressed as fractions). Division by zero fails.
The floating-point tolerance is the larger of 1e-9 and eight machine epsilons
scaled to the result. This checks the calculation on supplied operands; it does
not prove that those operands were correctly extracted or that the operation is
appropriate. Rounded numbers belong in presentation, not in the oracle result.

Artifact references require an existing ID in the same run/project and its exact
SHA-256. The existing Artifact Store download verifier checks availability,
size and bytes before validation succeeds. Pending, missing, changed or out-of-scope
artifacts fail. Artifacts returned in the same worker completion are not available
as prior evidence: they cannot attest their own validity.

`ready` requires at least one claim. `insufficient` requires explicit limitations
and produces `review_required`, never a success. Invalid or insufficient results
terminate the stage/process before downstream steps or worker/result artifact
writes. Existing compensation behavior applies when earlier steps armed it.
There is no automatic repair or retry of the rejected business result.

The `business.output.validated` event records contract/schema/output hashes,
status, bounded reason codes and `factualClaims: human_review_required`. It omits
report text, document names, calculation operands and raw validator errors.
Technical completion, deterministic validation and human acceptance remain distinct.

Verification: unit and real store/lease tests cover malformed JSON/schema, false
arithmetic, insufficient evidence, duplicate IDs, invented/stale source references,
missing/tampered artifact bytes, restart and version pinning. Existing scheduler,
process builder, forms and internal-report v1 tests form the compatibility gate.
These are synthetic checks, not EVAL-101 trials or pilot/human acceptance.

Design references reviewed 25 October 2026:
[JSON Schema object validation](https://json-schema.org/understanding-json-schema/reference/object),
[Ajv strict mode](https://ajv.js.org/strict-mode),
[Ajv security considerations](https://ajv.js.org/security.html).
The design uses bundled schemas compiled once with strict validation, no data
mutation and no network schema resolution.
