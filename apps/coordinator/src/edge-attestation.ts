import { createHash } from "node:crypto";

import type { CoordinatorConfig } from "./config.js";
import type {
  EdgeAttestationProvider,
  EdgeAttestationVerdict,
  EdgePlatform,
  EdgeWorkerRegistration,
} from "./types.js";

const BROKER_RESPONSE_LIMIT_BYTES = 65_536;
const ATTESTATION_CLOCK_SKEW_MS = 5 * 60_000;

export interface EdgeAttestationChallengeRecord {
  id: string;
  challenge: string;
  challengeSha256: string;
  platform: EdgePlatform;
  applicationId: string;
  nodeName: string;
  expiresAt: string;
}

export interface EdgeAttestationRequest {
  challenge: EdgeAttestationChallengeRecord;
  registration: EdgeWorkerRegistration;
}

export interface EdgeAttestationVerifier {
  readonly available: boolean;
  readonly mode: "disabled" | "broker" | "test";
  readonly reason: string | null;
  verify(input: EdgeAttestationRequest): Promise<EdgeAttestationVerdict>;
}

export class EdgeAttestationError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

function expectedProvider(platform: EdgePlatform): EdgeAttestationProvider {
  return platform === "android" ? "play_integrity" : "app_attest";
}

function sha256(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function safeDate(value: unknown, field: string): Date {
  if (typeof value !== "string" || !value) throw new EdgeAttestationError(502, `Attestation broker не вернул ${field}`);
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.getTime())) throw new EdgeAttestationError(502, `Attestation broker вернул некорректный ${field}`);
  return parsed;
}

async function boundedJson(response: Response): Promise<unknown> {
  const announced = Number(response.headers.get("content-length") ?? 0);
  if (announced > BROKER_RESPONSE_LIMIT_BYTES) {
    throw new EdgeAttestationError(502, "Ответ attestation broker слишком большой");
  }
  if (!response.body) return null;
  const chunks: Uint8Array[] = [];
  let size = 0;
  const reader = response.body.getReader();
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > BROKER_RESPONSE_LIMIT_BYTES) {
      await reader.cancel();
      throw new EdgeAttestationError(502, "Ответ attestation broker слишком большой");
    }
    chunks.push(value);
  }
  const payload = Buffer.concat(chunks.map((chunk) => Buffer.from(chunk))).toString("utf8");
  try {
    return JSON.parse(payload) as unknown;
  } catch {
    throw new EdgeAttestationError(502, "Attestation broker вернул некорректный JSON");
  }
}

export interface BrokerEdgeAttestationOptions {
  url: string;
  token: string;
  timeoutSeconds: number;
  allowDevelopment: boolean;
  androidRequiredVerdicts: string[];
  iosRequiredVerdicts: string[];
  fetcher?: typeof fetch;
}

export class BrokerEdgeAttestationVerifier implements EdgeAttestationVerifier {
  readonly available = true;
  readonly mode = "broker" as const;
  readonly reason = null;
  private readonly fetcher: typeof fetch;

  constructor(private readonly options: BrokerEdgeAttestationOptions) {
    const endpoint = new URL(options.url);
    if (endpoint.protocol !== "https:") throw new Error("Edge attestation broker должен использовать HTTPS");
    if (endpoint.username || endpoint.password || endpoint.hash) {
      throw new Error("URL edge attestation broker не должен содержать credentials или fragment");
    }
    if (!options.token) throw new Error("Edge attestation broker token обязателен");
    this.fetcher = options.fetcher ?? fetch;
  }

  async verify(input: EdgeAttestationRequest): Promise<EdgeAttestationVerdict> {
    const provider = expectedProvider(input.challenge.platform);
    if (input.registration.platform !== input.challenge.platform
      || input.registration.attestation.provider !== provider
      || input.registration.attestation.applicationId !== input.challenge.applicationId
      || input.registration.name.trim() !== input.challenge.nodeName) {
      throw new EdgeAttestationError(400, "Attestation не соответствует выданному challenge");
    }
    const expectedHash = sha256(input.challenge.challenge);
    if (expectedHash !== input.challenge.challengeSha256) {
      throw new EdgeAttestationError(400, "Challenge повреждён");
    }

    let response: Response;
    try {
      response = await this.fetcher(this.options.url, {
        method: "POST",
        redirect: "error",
        headers: {
          accept: "application/json",
          authorization: `Bearer ${this.options.token}`,
          "content-type": "application/json",
          "user-agent": "agat-coordinator/1.7.0",
        },
        body: JSON.stringify({
          schemaVersion: 1,
          platform: input.challenge.platform,
          provider,
          applicationId: input.challenge.applicationId,
          keyId: input.registration.attestation.keyId,
          challengeId: input.challenge.id,
          challenge: input.challenge.challenge,
          challengeSha256: input.challenge.challengeSha256,
          evidenceToken: input.registration.attestation.token,
        }),
        signal: AbortSignal.timeout(this.options.timeoutSeconds * 1_000),
      });
    } catch (error) {
      throw new EdgeAttestationError(
        502,
        error instanceof Error && error.name === "TimeoutError"
          ? "Attestation broker не ответил вовремя"
          : "Attestation broker недоступен",
      );
    }
    const payload = await boundedJson(response);
    if (!response.ok) throw new EdgeAttestationError(502, `Attestation broker отклонил запрос (${response.status})`);
    return this.validateVerdict(payload, input);
  }

  private validateVerdict(payload: unknown, input: EdgeAttestationRequest): EdgeAttestationVerdict {
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new EdgeAttestationError(502, "Attestation broker вернул некорректный verdict");
    }
    const verdict = payload as Partial<EdgeAttestationVerdict>;
    const provider = expectedProvider(input.challenge.platform);
    if (verdict.valid !== true) throw new EdgeAttestationError(403, "Аппаратная attestation отклонена");
    if (verdict.schemaVersion !== 1
      || verdict.platform !== input.challenge.platform
      || verdict.provider !== provider
      || verdict.applicationId !== input.challenge.applicationId
      || verdict.keyId !== input.registration.attestation.keyId
      || verdict.challengeSha256 !== input.challenge.challengeSha256
      || verdict.hardwareBacked !== true) {
      throw new EdgeAttestationError(502, "Verdict broker не привязан к challenge или устройству");
    }
    if (verdict.environment !== "production" && verdict.environment !== "development") {
      throw new EdgeAttestationError(502, "Attestation broker не указал environment");
    }
    if (verdict.environment === "development" && !this.options.allowDevelopment) {
      throw new EdgeAttestationError(403, "Development attestation запрещена policy coordinator");
    }
    const issuedAt = safeDate(verdict.issuedAt, "issuedAt");
    const expiresAt = safeDate(verdict.expiresAt, "expiresAt");
    const now = Date.now();
    if (issuedAt.getTime() > now + ATTESTATION_CLOCK_SKEW_MS
      || expiresAt.getTime() <= issuedAt.getTime()
      || expiresAt.getTime() <= now
      || expiresAt.getTime() - issuedAt.getTime() > 15 * 60_000) {
      throw new EdgeAttestationError(403, "Attestation verdict истёк или имеет недопустимый срок");
    }
    if (!Array.isArray(verdict.verdicts) || verdict.verdicts.some((item) => typeof item !== "string")) {
      throw new EdgeAttestationError(502, "Attestation broker не вернул verdicts");
    }
    const required = input.challenge.platform === "android"
      ? this.options.androidRequiredVerdicts
      : this.options.iosRequiredVerdicts;
    const present = new Set(verdict.verdicts);
    if (required.some((item) => !present.has(item))) {
      throw new EdgeAttestationError(403, "Attestation не прошла обязательные integrity verdicts");
    }
    return verdict as EdgeAttestationVerdict;
  }
}

class DisabledEdgeAttestationVerifier implements EdgeAttestationVerifier {
  readonly available = false;
  readonly mode = "disabled" as const;
  readonly reason = "Attestation broker не настроен";

  async verify(): Promise<EdgeAttestationVerdict> {
    throw new EdgeAttestationError(503, this.reason);
  }
}

export function createEdgeAttestationVerifier(config: CoordinatorConfig): EdgeAttestationVerifier {
  if (!config.edgeEnabled || config.edgeAttestationMode === "disabled") {
    return new DisabledEdgeAttestationVerifier();
  }
  return new BrokerEdgeAttestationVerifier({
    url: config.edgeAttestationBrokerUrl,
    token: config.edgeAttestationBrokerToken,
    timeoutSeconds: config.edgeAttestationTimeoutSeconds,
    allowDevelopment: config.edgeAllowDevelopmentAttestation,
    androidRequiredVerdicts: config.edgeAndroidRequiredVerdicts,
    iosRequiredVerdicts: config.edgeIosRequiredVerdicts,
  });
}
