#!/usr/bin/env node

import { createPrivateKey, sign } from "node:crypto";
import fs from "node:fs";

function canonicalJson(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  return `{${Object.entries(value)
    .filter(([, field]) => field !== undefined)
    .sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0)
    .map(([key, field]) => `${JSON.stringify(key)}:${canonicalJson(field)}`)
    .join(",")}}`;
}

function fail(message) {
  process.stderr.write(`${message}\n`);
  process.exit(2);
}

const [statementPath, privateKeyPath, keyId] = process.argv.slice(2);
if (!statementPath || !privateKeyPath || !keyId) {
  fail("Usage: sign-worker-provenance.mjs <statement.json> <ed25519-private-key.pem> <key-id>");
}
if (!/^[a-z0-9][a-z0-9._-]{0,62}$/.test(keyId)) fail("key-id has an invalid format");

let statement;
try {
  statement = JSON.parse(fs.readFileSync(statementPath, "utf8"));
} catch (error) {
  fail(`Cannot read provenance statement: ${error instanceof Error ? error.message : String(error)}`);
}
if (!statement || statement.schemaVersion !== 1) fail("statement.schemaVersion must be 1");
if (statement.predicateType !== "https://slsa.dev/provenance/v1") {
  fail("statement.predicateType must be https://slsa.dev/provenance/v1");
}
for (const field of ["subjectDigest", "sigstoreBundleSha256", "sourceCommit", "verifiedAt", "expiresAt"]) {
  if (typeof statement[field] !== "string" || !statement[field]) fail(`statement.${field} is required`);
}
statement.policyId = String(statement.policyId ?? "").trim().toLowerCase();
statement.subjectDigest = statement.subjectDigest.toLowerCase();
statement.ociRepository = String(statement.ociRepository ?? "").trim().toLowerCase();
statement.sourceCommit = statement.sourceCommit.toLowerCase();
statement.sigstoreBundleSha256 = statement.sigstoreBundleSha256.toLowerCase();
if (!/^[a-z0-9][a-z0-9._-]{0,62}$/.test(statement.policyId)) fail("policyId has an invalid format");
if (!/^sha256:[0-9a-f]{64}$/.test(statement.subjectDigest)) fail("subjectDigest must be sha256:<64 hex>");
if (!/^[a-z0-9][a-z0-9._:-]*(?:\/[a-z0-9][a-z0-9._-]*)+$/.test(statement.ociRepository)
  || statement.ociRepository.includes("@") || statement.ociRepository.includes("//")) {
  fail("ociRepository must be registry/repository without tag or digest");
}
if (!/^[0-9a-f]{64}$/.test(statement.sigstoreBundleSha256)) fail("sigstoreBundleSha256 must be 64 hex");
if (!/^(?:[0-9a-f]{40}|[0-9a-f]{64})$/.test(statement.sourceCommit)) fail("sourceCommit must be full 40/64 hex");
for (const field of ["builderId", "buildType", "sourceRepository"]) {
  let parsed;
  try {
    parsed = new URL(statement[field]);
  } catch {
    fail(`statement.${field} must be an absolute HTTPS URL`);
  }
  if (parsed.protocol !== "https:" || parsed.username || parsed.password || parsed.hash) {
    fail(`statement.${field} must be HTTPS without credentials or fragment`);
  }
  statement[field] = parsed.toString();
}
const verifiedTime = new Date(statement.verifiedAt).getTime();
const expiresTime = new Date(statement.expiresAt).getTime();
if (!Number.isFinite(verifiedTime) || !Number.isFinite(expiresTime)) fail("verifiedAt/expiresAt must be valid timestamps");
if (verifiedTime > Date.now() + 300_000) fail("verifiedAt is too far in the future");
if (expiresTime <= Date.now() || expiresTime <= verifiedTime) fail("expiresAt must be after verifiedAt and in the future");
statement.verifiedAt = new Date(verifiedTime).toISOString();
statement.expiresAt = new Date(expiresTime).toISOString();
if (expiresTime - verifiedTime > 31 * 24 * 60 * 60 * 1_000) {
  fail("provenance admission lifetime cannot exceed 31 days");
}
statement = {
  schemaVersion: 1,
  policyId: statement.policyId,
  subjectDigest: statement.subjectDigest,
  ociRepository: statement.ociRepository,
  predicateType: statement.predicateType,
  builderId: statement.builderId,
  buildType: statement.buildType,
  sourceRepository: statement.sourceRepository,
  sourceCommit: statement.sourceCommit,
  sigstoreBundleSha256: statement.sigstoreBundleSha256,
  verifiedAt: statement.verifiedAt,
  expiresAt: statement.expiresAt,
};

let privateKey;
try {
  privateKey = createPrivateKey(fs.readFileSync(privateKeyPath));
} catch (error) {
  fail(`Cannot read private key: ${error instanceof Error ? error.message : String(error)}`);
}
if (privateKey.asymmetricKeyType !== "ed25519") fail("Private key must be Ed25519");
const serialized = canonicalJson(statement);
const signature = sign(null, Buffer.from(serialized, "utf8"), privateKey).toString("base64");
process.stdout.write(`${JSON.stringify({ statement, keyId, signature }, null, 2)}\n`);
