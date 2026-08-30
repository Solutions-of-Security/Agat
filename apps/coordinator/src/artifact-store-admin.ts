import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { loadConfig, validateTemporalCoordinatorConfig, type CoordinatorConfig } from "./config.js";
import { AgatStore } from "./database.js";
import type { ArtifactObjectStoreOptions } from "./artifact-object-store.js";

type AdminMode = "inspect" | "configure-lifecycle" | "migrate" | "reconcile" | "lifecycle" | "protect";

interface AdminArguments {
  mode: AdminMode;
  apply: boolean;
  confirmation: string;
  limit: number;
  graceSeconds: number;
  maximum: number;
  noncurrentExpirationDays: number;
  abortMultipartDays: number;
  reportPath: string;
  artifactId: string;
  projectId: string;
  retentionUntil: string;
  legalHold: boolean;
  actor: string;
}

function integer(value: string | undefined, field: string, fallback: number): number {
  if (value === undefined) return fallback;
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed)) throw new Error(`${field} должен быть целым числом`);
  return parsed;
}

function parseArguments(argv: string[]): AdminArguments {
  const mode = argv[0] as AdminMode | undefined;
  if (!mode || !(["inspect", "configure-lifecycle", "migrate", "reconcile", "lifecycle", "protect"] as const).includes(mode)) {
    throw new Error("Использование: artifact-store-admin <inspect|configure-lifecycle|migrate|reconcile|lifecycle|protect> [options]");
  }
  const values = new Map<string, string>();
  let apply = false;
  for (let index = 1; index < argv.length; index += 1) {
    const argument = argv[index]!;
    if (argument === "--apply") {
      apply = true;
      continue;
    }
    if (!argument.startsWith("--") || index + 1 >= argv.length) throw new Error(`Неизвестный argument: ${argument}`);
    values.set(argument.slice(2), argv[index + 1]!);
    index += 1;
  }
  return {
    mode,
    apply,
    confirmation: values.get("confirm") ?? "",
    limit: integer(values.get("limit"), "--limit", 20),
    graceSeconds: integer(values.get("grace-seconds"), "--grace-seconds", 86_400),
    maximum: integer(values.get("maximum"), "--maximum", 10_000),
    noncurrentExpirationDays: integer(values.get("noncurrent-expiration-days"), "--noncurrent-expiration-days", 30),
    abortMultipartDays: integer(values.get("abort-multipart-days"), "--abort-multipart-days", 1),
    reportPath: values.get("report") ?? "",
    artifactId: values.get("artifact-id") ?? "",
    projectId: values.get("project-id") ?? "",
    retentionUntil: values.get("retention-until") ?? "",
    legalHold: values.get("legal-hold") === "true",
    actor: values.get("actor") ?? "",
  };
}

function s3Options(config: CoordinatorConfig): ArtifactObjectStoreOptions {
  return {
    endpoint: config.artifactS3Endpoint,
    region: config.artifactS3Region,
    bucket: config.artifactS3Bucket,
    prefix: config.artifactS3Prefix,
    forcePathStyle: config.artifactS3ForcePathStyle,
    ...(config.artifactS3AccessKeyId ? {
      accessKeyId: config.artifactS3AccessKeyId,
      secretAccessKey: config.artifactS3SecretAccessKey,
      ...(config.artifactS3SessionToken ? { sessionToken: config.artifactS3SessionToken } : {}),
    } : {}),
    requestTimeoutMs: config.artifactS3RequestTimeoutMs,
    maximumObjectBytes: config.artifactS3MaximumObjectBytes,
    serverSideEncryption: config.artifactS3Sse,
    ...(config.artifactS3KmsKeyId ? { kmsKeyId: config.artifactS3KmsKeyId } : {}),
    objectLockMode: config.artifactS3ObjectLockMode,
    requireVersioning: config.artifactS3RequireVersioning,
  };
}

function postgresOptions(config: CoordinatorConfig) {
  return {
    systemUrl: config.postgresUrl,
    tenantUrl: config.postgresTenantUrl,
    roleMode: "runtime" as const,
    applicationName: `agat-artifact-admin-${process.pid}`,
    poolMax: 1,
    connectTimeoutMs: config.postgresConnectTimeoutMs,
    idleTimeoutMs: config.postgresIdleTimeoutMs,
    statementTimeoutMs: config.postgresStatementTimeoutMs,
    sslMode: config.postgresSslMode,
    sslCa: config.postgresCaCertPath ? fs.readFileSync(config.postgresCaCertPath, "utf8") : "",
    sslCert: config.postgresClientCertPath ? fs.readFileSync(config.postgresClientCertPath, "utf8") : "",
    sslKey: config.postgresClientKeyPath ? fs.readFileSync(config.postgresClientKeyPath, "utf8") : "",
  };
}

function writeReport(reportPath: string, report: Record<string, unknown>): void {
  if (!reportPath) return;
  const target = path.resolve(reportPath);
  fs.mkdirSync(path.dirname(target), { recursive: true, mode: 0o700 });
  const temporary = `${target}.${process.pid}.tmp`;
  try {
    fs.writeFileSync(temporary, `${JSON.stringify(report, null, 2)}\n`, { flag: "wx", mode: 0o600 });
    fs.renameSync(temporary, target);
    fs.chmodSync(target, 0o600);
  } finally {
    if (fs.existsSync(temporary)) fs.rmSync(temporary, { force: true });
  }
}

function requireConfirmation(actual: string, expected: string): void {
  if (actual !== expected) throw new Error(`Операция требует --confirm ${expected}`);
}

export function runArtifactStoreAdmin(argv: string[]): Record<string, unknown> {
  const args = parseArguments(argv);
  const config = loadConfig();
  validateTemporalCoordinatorConfig({
    ...config,
    temporalEnabled: false,
    siemEnabled: false,
    requireSignedWorkerReleases: false,
  });
  if (config.stateStoreDriver !== "postgresql" || config.artifactStoreDriver !== "s3") {
    throw new Error("Artifact Store admin требует AGAT_STATE_STORE_DRIVER=postgresql и AGAT_ARTIFACT_STORE_DRIVER=s3");
  }
  const store = new AgatStore(":postgresql:", {
    stateStoreDriver: "postgresql",
    postgres: postgresOptions(config),
    postgresSchemaMode: "runtime",
    postgresRuntimeRole: decodeURIComponent(new URL(config.postgresUrl).username),
    schemaOnly: true,
    requirePostgresAdmission: true,
    coordinatorInstanceId: `artifact-admin-${process.pid}`,
    region: config.region,
    residencyDomain: config.residencyDomain,
    regionLossDrActivationId: config.regionLossDrActivationId,
    regionLossDrWriteEpoch: config.regionLossDrWriteEpoch,
    artifactStoreDriver: "s3",
    artifactRetentionDays: config.artifactRetentionDays,
    artifactS3: s3Options(config),
  });
  try {
    let result: Record<string, unknown>;
    if (args.mode === "inspect") {
      result = store.inspectArtifactBucket();
    } else if (args.mode === "configure-lifecycle") {
      requireConfirmation(args.confirmation, "APPLY_LIFECYCLE");
      result = store.configureArtifactBucketLifecycle(args.noncurrentExpirationDays, args.abortMultipartDays);
    } else if (args.mode === "migrate") {
      requireConfirmation(args.confirmation, "MIGRATE_POSTGRES_ARTIFACTS");
      result = store.migratePostgresArtifactsToS3(args.limit);
    } else if (args.mode === "reconcile") {
      if (args.apply) requireConfirmation(args.confirmation, "DELETE_ORPHANS_AND_QUARANTINE_MISSING");
      result = store.reconcileArtifactObjects(args.graceSeconds, args.apply, args.maximum);
    } else if (args.mode === "lifecycle") {
      requireConfirmation(args.confirmation, "RUN_ARTIFACT_LIFECYCLE");
      result = store.runArtifactLifecycle(args.limit);
    } else {
      requireConfirmation(args.confirmation, "UPDATE_ARTIFACT_PROTECTION");
      if (!args.artifactId || !args.projectId || !args.retentionUntil || !args.actor) {
        throw new Error("protect требует --artifact-id, --project-id, --retention-until и --actor");
      }
      result = store.setArtifactRetention(
        args.artifactId,
        args.projectId,
        args.retentionUntil,
        args.legalHold,
        args.actor,
      );
    }
    const report = {
      schemaVersion: 1,
      mode: args.mode,
      apply: args.apply || args.mode !== "reconcile",
      generatedAt: new Date().toISOString(),
      bucket: config.artifactS3Bucket,
      prefix: config.artifactS3Prefix,
      result,
    };
    writeReport(args.reportPath, report);
    return report;
  } finally {
    store.close();
  }
}

async function main(): Promise<void> {
  const report = runArtifactStoreAdmin(process.argv.slice(2));
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : "Artifact Store admin failed"}\n`);
    process.exitCode = 1;
  });
}
