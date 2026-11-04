# Business input and effective prompt: SP-002, second increment

Implemented 25 October 2026 after the first
[output validation increment](./business-output-contracts.md).
A start node may select `config.inputContract: "report-request.v1"`:

```json
{
  "schemaVersion": "report-request.v1",
  "question": "Compare request volume and resolution time",
  "period": "July–August 2026",
  "requirements": ["Do not infer causes from aggregate counts"]
}
```

All four fields are mandatory. Question and requirements have bounded nonblank
strings; period is explicit, with a maximum of 160 characters. At most 20 unique
requirements are allowed. The whole input has a 100,000 UTF-8 byte bound. Unknown
fields, coercion, raw text and malformed JSON fail before creating a run or child
instance. Input data cannot grant authority: selected collections, tools and
permissions remain in the existing process/project control plane.

Validation happens at the shared start boundary used by manual starts, scheduled
starts, subprocesses and instance replay. Partial starts and start-node templates
are incompatible with this contract. An invalid subprocess stops its parent with
the existing failure/compensation behavior and creates no invalid child.
Successful inputs emit only contract/schema/input hashes and validation status in
`business.input.validated`; their question and requirements are not copied there.

Output contracts now append the bundled schema and generation instructions to
the effective system prompt **before** capturing its immutable agent snapshot.
The lease and execution manifest contain the same prompt; the catalog agent is
not modified. A schema descriptor/hash is pinned in stage activity and included
in the manifest. Schema revision drift fails closed. The same output contract is
also enforced when testing an individual agent node.

Instructions apply through the existing OpenAI-compatible model path. Native
provider constrained decoding is not assumed or silently enabled; malformed model
responses still fail server validation. This follows the applicable guidance to
include the schema in the prompt and independently validate the response in
[Ollama structured outputs](https://github.com/ollama/ollama/blob/main/docs/capabilities/structured-outputs.mdx).
No native capability claim or real-model qualification is made by these tests.

Verification covers strict schema/UTF-8 limits, BPMN, rejected placement/templates,
no persistence on invalid start, HTTP authentication/start, schema/prompt manifest
identity, schema drift, node tests, subprocess failure and replay version pinning.
Legacy input and text-agent paths are covered by the scheduler/process/preflight
regression suite. Browser forms for generic solution contracts remain USE-007;
new pack installation/version selection remains SP-001/SP-005.
