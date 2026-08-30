import path from "node:path";
import { pathToFileURL } from "node:url";

import {
  CreateBucketCommand,
  GetBucketVersioningCommand,
  HeadBucketCommand,
  PutBucketVersioningCommand,
  S3Client,
} from "@aws-sdk/client-s3";

function required(name: string): string {
  const value = process.env[name]?.trim() ?? "";
  if (!value) throw new Error(`${name} обязателен`);
  return value;
}

function absent(error: unknown): boolean {
  const row = error && typeof error === "object" ? error as Record<string, unknown> : {};
  const metadata = row.$metadata && typeof row.$metadata === "object"
    ? row.$metadata as Record<string, unknown>
    : {};
  return metadata.httpStatusCode === 404 || ["NoSuchBucket", "NotFound"].includes(String(row.name ?? row.Code ?? row.code ?? ""));
}

export async function bootstrapArtifactBucket(mode: "apply" | "verify"): Promise<Record<string, unknown>> {
  const endpoint = required("AGAT_ARTIFACT_S3_ENDPOINT").replace(/\/+$/, "");
  const region = required("AGAT_ARTIFACT_S3_REGION");
  const bucket = required("AGAT_ARTIFACT_S3_BUCKET");
  const accessKeyId = required("AGAT_ARTIFACT_S3_ACCESS_KEY_ID");
  const secretAccessKey = required("AGAT_ARTIFACT_S3_SECRET_ACCESS_KEY");
  const url = new URL(endpoint);
  if (url.protocol !== "https:" && !(["localhost", "127.0.0.1", "::1", "agat-artifact-store"].includes(url.hostname)
    && url.protocol === "http:")) {
    throw new Error("Artifact bucket bootstrap требует HTTPS; HTTP разрешён только local MinIO");
  }
  if (!/^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$/.test(bucket) || bucket.includes("..")) {
    throw new Error("AGAT_ARTIFACT_S3_BUCKET имеет небезопасное имя");
  }
  const client = new S3Client({
    endpoint,
    region,
    forcePathStyle: process.env.AGAT_ARTIFACT_S3_FORCE_PATH_STYLE !== "false",
    credentials: { accessKeyId, secretAccessKey },
    maxAttempts: 3,
  });
  let created = false;
  try {
    try {
      await client.send(new HeadBucketCommand({ Bucket: bucket }));
    } catch (error) {
      if (mode !== "apply" || !absent(error)) throw error;
      await client.send(new CreateBucketCommand({ Bucket: bucket }));
      created = true;
    }
    let versioning = await client.send(new GetBucketVersioningCommand({ Bucket: bucket }));
    if (versioning.Status !== "Enabled" && mode === "apply") {
      await client.send(new PutBucketVersioningCommand({
        Bucket: bucket,
        VersioningConfiguration: { Status: "Enabled" },
      }));
      versioning = await client.send(new GetBucketVersioningCommand({ Bucket: bucket }));
    }
    if (versioning.Status !== "Enabled") throw new Error("Artifact bucket versioning не включён");
    return {
      schemaVersion: 1,
      mode,
      bucket,
      created,
      versioning: versioning.Status,
      checkedAt: new Date().toISOString(),
    };
  } finally {
    client.destroy();
  }
}

async function main(): Promise<void> {
  const mode = process.argv[2];
  if (mode !== "apply" && mode !== "verify") throw new Error("Использование: artifact-store-bootstrap <apply|verify>");
  process.stdout.write(`${JSON.stringify(await bootstrapArtifactBucket(mode))}\n`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : "Artifact bucket bootstrap failed"}\n`);
    process.exitCode = 1;
  });
}
