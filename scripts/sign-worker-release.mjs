#!/usr/bin/env node

import { createPrivateKey, sign } from "node:crypto";
import fs from "node:fs";

function usage() {
  process.stderr.write("Usage: node scripts/sign-worker-release.mjs <manifest.json> <ed25519-private-key.pem> <key-id>\n");
  process.exitCode = 2;
}

function canonicalJson(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const entries = Object.entries(value)
    .filter(([, field]) => field !== undefined)
    .sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0);
  return `{${entries.map(([key, field]) => `${JSON.stringify(key)}:${canonicalJson(field)}`).join(",")}}`;
}

function identifier(value, field) {
  const normalized = typeof value === "string" ? value.trim().toLowerCase() : "";
  if (!/^[a-z0-9][a-z0-9._-]{0,62}$/.test(normalized)) {
    throw new Error(`${field} must contain 1..63 characters [a-z0-9._-]`);
  }
  return normalized;
}

function requiredText(value, field, maxLength) {
  const normalized = typeof value === "string" ? value.trim() : "";
  if (!normalized || normalized.length > maxLength || /[\r\n\0]/.test(normalized)) {
    throw new Error(`${field} is missing or invalid`);
  }
  return normalized;
}

function normalizeManifest(input) {
  if (!input || typeof input !== "object" || input.schemaVersion !== 1) {
    throw new Error("manifest.schemaVersion must be 1");
  }
  const releaseId = identifier(input.releaseId, "releaseId");
  const version = requiredText(input.version, "version", 80);
  if (!/^[0-9A-Za-z][0-9A-Za-z.+_-]{0,79}$/.test(version)) throw new Error("version has an invalid format");
  const artifactDigest = typeof input.artifactDigest === "string" ? input.artifactDigest.trim().toLowerCase() : "";
  if (!/^sha256:[0-9a-f]{64}$/.test(artifactDigest)) throw new Error("artifactDigest must be sha256:<64 hex>");
  if (!Array.isArray(input.platforms) || input.platforms.length === 0 || input.platforms.length > 16) {
    throw new Error("platforms must contain 1..16 platform families");
  }
  const platforms = [...new Set(input.platforms.map((platform) => identifier(platform, "platform")))].sort();
  const issuedAt = new Date(input.issuedAt).toISOString();
  if (new Date(issuedAt).getTime() > Date.now() + 300_000) throw new Error("issuedAt is too far in the future");
  const expiresAt = input.expiresAt ? new Date(input.expiresAt).toISOString() : null;
  if (expiresAt && expiresAt <= new Date().toISOString()) throw new Error("release already expired");
  const metadataEntries = Object.entries(input.metadata ?? {});
  if (metadataEntries.length > 32) throw new Error("metadata contains more than 32 fields");
  const metadata = Object.fromEntries(metadataEntries
    .sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0)
    .map(([key, value]) => {
      const normalizedKey = identifier(key, "metadata key");
      if (typeof value !== "string" || value.length > 500 || /[\r\n\0]/.test(value)) {
        throw new Error(`metadata ${normalizedKey} is invalid`);
      }
      return [normalizedKey, value];
    }));
  return { schemaVersion: 1, releaseId, version, artifactDigest, platforms, issuedAt, expiresAt, metadata };
}

const [manifestPath, privateKeyPath, rawKeyId, ...extra] = process.argv.slice(2);
if (!manifestPath || !privateKeyPath || !rawKeyId || extra.length > 0) {
  usage();
} else {
  try {
    const keyId = identifier(rawKeyId, "keyId");
    const manifest = normalizeManifest(JSON.parse(fs.readFileSync(manifestPath, "utf8")));
    const privateKey = createPrivateKey(fs.readFileSync(privateKeyPath));
    if (privateKey.asymmetricKeyType !== "ed25519") throw new Error("private key must be Ed25519");
    const signature = sign(null, Buffer.from(canonicalJson(manifest), "utf8"), privateKey).toString("base64");
    process.stdout.write(`${JSON.stringify({ manifest, keyId, signature }, null, 2)}\n`);
  } catch (error) {
    process.stderr.write(`Worker release signing failed: ${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  }
}
