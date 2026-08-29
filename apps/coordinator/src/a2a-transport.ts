import { createHash } from "node:crypto";
import { lookup } from "node:dns/promises";
import http from "node:http";
import https from "node:https";
import { BlockList, isIP } from "node:net";

import {
  A2A_MEDIA_TYPE,
  A2A_PROTOCOL_VERSION,
} from "./a2a.js";
import type {
  A2ARemoteAuthMaterial,
  A2ARemoteConnection,
} from "./types.js";

const LOOPBACK_HOSTS = new Set(["127.0.0.1", "::1", "localhost"]);
const MEDIA_TYPE = /^[a-z0-9][a-z0-9!#$&^_.+-]{0,126}\/[a-z0-9][a-z0-9!#$&^_.+-]{0,126}$/;
const BLOCKED_ADDRESSES = new BlockList();

for (const [network, prefix] of [
  ["0.0.0.0", 8],
  ["10.0.0.0", 8],
  ["100.64.0.0", 10],
  ["127.0.0.0", 8],
  ["169.254.0.0", 16],
  ["172.16.0.0", 12],
  ["192.0.0.0", 24],
  ["192.0.2.0", 24],
  ["192.168.0.0", 16],
  ["192.88.99.0", 24],
  ["198.18.0.0", 15],
  ["198.51.100.0", 24],
  ["203.0.113.0", 24],
  ["224.0.0.0", 4],
  ["240.0.0.0", 4],
] as const) {
  BLOCKED_ADDRESSES.addSubnet(network, prefix, "ipv4");
}
for (const [network, prefix] of [
  ["::", 128],
  ["::1", 128],
  ["::ffff:0:0", 96],
  ["64:ff9b::", 96],
  ["100::", 64],
  ["fc00::", 7],
  ["fe80::", 10],
  ["ff00::", 8],
  ["2001:db8::", 32],
] as const) {
  BLOCKED_ADDRESSES.addSubnet(network, prefix, "ipv6");
}

export interface A2AOutboundPolicy {
  allowLoopback: boolean;
  timeoutMs: number;
  maxResponseBytes: number;
}

export interface A2ATransportResponse {
  status: number;
  headers: http.IncomingHttpHeaders;
  body: Buffer;
}

export interface DiscoveredA2ARemote {
  name: string;
  description: string;
  agentCardUrl: string;
  interfaceUrl: string;
  protocolVersion: string;
  tenant: string | null;
  skillId: string;
  skillName: string;
  inputModes: string[];
  outputModes: string[];
  capabilities: {
    streaming: boolean;
    pushNotifications: boolean;
  };
  cardSha256: string;
}

export class A2ATransportError extends Error {
  constructor(
    message: string,
    readonly httpStatus = 502,
    readonly upstreamStatus: number | null = null,
  ) {
    super(message);
  }
}

function normalizedExternalUrl(value: string, label: string, allowQuery: boolean): URL {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new A2ATransportError(`${label} должен быть абсолютным HTTP(S) URL`, 400);
  }
  if (!url.hostname || url.username || url.password || url.hash || (!allowQuery && url.search)) {
    throw new A2ATransportError(`${label} не должен содержать credentials, fragment${allowQuery ? "" : " или query"}`, 400);
  }
  if (url.protocol !== "https:" && !(url.protocol === "http:" && LOOPBACK_HOSTS.has(normalizedHostname(url)))) {
    throw new A2ATransportError(`${label} требует HTTPS; HTTP разрешён только для loopback`, 400);
  }
  return url;
}

function normalizedHostname(url: URL): string {
  return url.hostname.startsWith("[") && url.hostname.endsWith("]")
    ? url.hostname.slice(1, -1)
    : url.hostname;
}

function addressBlocked(address: string): boolean {
  const family = isIP(address);
  if (family === 4) return BLOCKED_ADDRESSES.check(address, "ipv4");
  if (family === 6) return BLOCKED_ADDRESSES.check(address, "ipv6");
  return true;
}

async function pinnedAddress(url: URL, policy: A2AOutboundPolicy): Promise<{ address: string; family: 4 | 6 }> {
  const hostname = normalizedHostname(url);
  const directFamily = isIP(hostname);
  if (directFamily) {
    if (addressBlocked(hostname) && !(policy.allowLoopback && LOOPBACK_HOSTS.has(hostname))) {
      throw new A2ATransportError("Outbound A2A URL указывает на запрещённый сетевой диапазон", 400);
    }
    return { address: hostname, family: directFamily as 4 | 6 };
  }
  if (LOOPBACK_HOSTS.has(hostname)) {
    if (!policy.allowLoopback) throw new A2ATransportError("Loopback outbound A2A выключен policy", 400);
    return { address: "127.0.0.1", family: 4 };
  }
  let addresses: Array<{ address: string; family: 4 | 6 }>;
  try {
    addresses = (await lookup(hostname, { all: true, verbatim: true }))
      .map((candidate) => ({
        address: candidate.address,
        family: candidate.family === 6 ? 6 as const : 4 as const,
      }));
  } catch {
    throw new A2ATransportError("Не удалось разрешить hostname outbound A2A", 502);
  }
  if (!addresses.length || addresses.some((candidate) => addressBlocked(candidate.address))) {
    throw new A2ATransportError("Outbound A2A hostname разрешается в запрещённый сетевой диапазон", 400);
  }
  const selected = addresses[0]!;
  return { address: selected.address, family: selected.family };
}

export async function validateA2AOutboundTarget(
  rawUrl: string,
  policy: A2AOutboundPolicy,
  allowQuery = false,
): Promise<void> {
  const url = normalizedExternalUrl(rawUrl, "Outbound A2A URL", allowQuery);
  await pinnedAddress(url, policy);
}

export async function a2aHttpRequest(
  rawUrl: string,
  input: {
    method: "GET" | "POST" | "DELETE";
    headers?: Record<string, string>;
    body?: Buffer | string;
    allowQuery?: boolean;
  },
  policy: A2AOutboundPolicy,
): Promise<A2ATransportResponse> {
  const url = normalizedExternalUrl(rawUrl, "Outbound A2A URL", input.allowQuery === true);
  const target = await pinnedAddress(url, policy);
  const hostname = normalizedHostname(url);
  const body = input.body === undefined
    ? null
    : typeof input.body === "string" ? Buffer.from(input.body, "utf8") : input.body;
  const requestHeaders: Record<string, string> = {
    accept: A2A_MEDIA_TYPE,
    host: url.host,
    ...input.headers,
  };
  if (body) requestHeaders["content-length"] = String(body.byteLength);
  const requestImpl = url.protocol === "https:" ? https.request : http.request;
  return new Promise<A2ATransportResponse>((resolve, reject) => {
    const request = requestImpl({
      protocol: url.protocol,
      hostname: target.address,
      family: target.family,
      port: url.port || undefined,
      method: input.method,
      path: `${url.pathname}${url.search}`,
      headers: requestHeaders,
      ...(url.protocol === "https:" ? { servername: hostname, rejectUnauthorized: true } : {}),
    }, (response) => {
      const chunks: Buffer[] = [];
      let size = 0;
      const advertised = Number.parseInt(response.headers["content-length"] ?? "", 10);
      if (Number.isFinite(advertised) && advertised > policy.maxResponseBytes) {
        response.destroy();
        reject(new A2ATransportError("Ответ outbound A2A превышает policy размера"));
        return;
      }
      response.on("data", (chunk: Buffer) => {
        size += chunk.byteLength;
        if (size > policy.maxResponseBytes) {
          response.destroy(new A2ATransportError("Ответ outbound A2A превышает policy размера"));
          return;
        }
        chunks.push(chunk);
      });
      response.on("end", () => resolve({
        status: response.statusCode ?? 502,
        headers: response.headers,
        body: Buffer.concat(chunks),
      }));
      response.on("error", reject);
    });
    request.setTimeout(policy.timeoutMs, () => request.destroy(new A2ATransportError("Timeout outbound A2A")));
    request.on("error", (error) => reject(error instanceof A2ATransportError
      ? error
      : new A2ATransportError(`Transport outbound A2A недоступен: ${error.message}`)));
    if (body) request.write(body);
    request.end();
  });
}

function parseJsonObject(response: A2ATransportResponse, operation: string): Record<string, unknown> {
  let payload: unknown;
  try {
    payload = JSON.parse(response.body.toString("utf8"));
  } catch {
    throw new A2ATransportError(`${operation} вернул невалидный JSON`, 502, response.status);
  }
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new A2ATransportError(`${operation} вернул не JSON-объект`, 502, response.status);
  }
  if (response.status < 200 || response.status >= 300) {
    throw new A2ATransportError(`${operation} вернул HTTP ${response.status}`, 502, response.status);
  }
  return payload as Record<string, unknown>;
}

function mediaTypeArray(value: unknown, fallback: string[], field: string): string[] {
  if (!Array.isArray(value)) return fallback;
  if (value.length > 64 || value.some((item) => typeof item !== "string" || !MEDIA_TYPE.test(item.trim().toLowerCase()))) {
    throw new A2ATransportError(`${field} в Agent Card содержит некорректный media type`, 400);
  }
  const result = (value as string[]).map((item) => item.trim().toLowerCase());
  return result.length ? [...new Set(result)] : fallback;
}

function cardIdentifier(value: unknown, field: string, maxLength: number): string {
  if (typeof value !== "string" || !value.trim() || value.trim().length > maxLength || /[\u0000-\u001f\u007f]/.test(value)) {
    throw new A2ATransportError(`${field} в Agent Card некорректен`, 400);
  }
  return value.trim();
}

export async function discoverA2ARemote(
  agentCardUrl: string,
  skillId: string | undefined,
  policy: A2AOutboundPolicy,
): Promise<DiscoveredA2ARemote> {
  const normalizedCardUrl = normalizedExternalUrl(agentCardUrl, "Agent Card URL", false).toString();
  const response = await a2aHttpRequest(normalizedCardUrl, {
    method: "GET",
    headers: { accept: "application/json, application/a2a+json" },
  }, policy);
  const card = parseJsonObject(response, "Agent Card");
  const interfaces = Array.isArray(card.supportedInterfaces) ? card.supportedInterfaces : [];
  const selectedInterface = interfaces.find((candidate) => {
    if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) return false;
    const value = candidate as Record<string, unknown>;
    return value.protocolBinding === "HTTP+JSON" && value.protocolVersion === A2A_PROTOCOL_VERSION;
  }) as Record<string, unknown> | undefined;
  if (!selectedInterface || typeof selectedInterface.url !== "string") {
    throw new A2ATransportError(`Agent Card не объявляет HTTP+JSON ${A2A_PROTOCOL_VERSION}`, 400);
  }
  const interfaceTarget = normalizedExternalUrl(selectedInterface.url, "A2A interface URL", false);
  await pinnedAddress(interfaceTarget, policy);
  const interfaceUrl = interfaceTarget.toString().replace(/\/+$/, "");
  const skills = Array.isArray(card.skills) ? card.skills.filter((item): item is Record<string, unknown> => (
    Boolean(item) && typeof item === "object" && !Array.isArray(item)
  )) : [];
  const selectedSkill = skillId
    ? skills.find((skill) => skill.id === skillId)
    : skills[0];
  if (!selectedSkill) {
    throw new A2ATransportError("Указанный A2A skill отсутствует в Agent Card", 400);
  }
  const discoveredSkillId = cardIdentifier(selectedSkill.id, "skills[].id", 200);
  const discoveredSkillName = cardIdentifier(selectedSkill.name, "skills[].name", 120);
  const tenant = selectedInterface.tenant === undefined || selectedInterface.tenant === ""
    ? null
    : cardIdentifier(selectedInterface.tenant, "supportedInterfaces[].tenant", 200);
  const capabilities = card.capabilities && typeof card.capabilities === "object" && !Array.isArray(card.capabilities)
    ? card.capabilities as Record<string, unknown>
    : {};
  const defaultInputModes = mediaTypeArray(card.defaultInputModes, ["text/plain"], "defaultInputModes");
  const defaultOutputModes = mediaTypeArray(card.defaultOutputModes, ["text/plain"], "defaultOutputModes");
  return {
    name: typeof card.name === "string" && card.name.trim()
      ? cardIdentifier(card.name, "name", 120)
      : discoveredSkillName,
    description: typeof card.description === "string" ? card.description.trim().slice(0, 2_000) : "",
    agentCardUrl: normalizedCardUrl,
    interfaceUrl,
    protocolVersion: A2A_PROTOCOL_VERSION,
    tenant,
    skillId: discoveredSkillId,
    skillName: discoveredSkillName,
    inputModes: mediaTypeArray(selectedSkill.inputModes, defaultInputModes, "skills[].inputModes"),
    outputModes: mediaTypeArray(selectedSkill.outputModes, defaultOutputModes, "skills[].outputModes"),
    capabilities: {
      streaming: capabilities.streaming === true,
      pushNotifications: capabilities.pushNotifications === true,
    },
    cardSha256: createHash("sha256").update(response.body).digest("hex"),
  };
}

export async function outboundAuthorization(
  auth: A2ARemoteAuthMaterial,
  subjectToken: string | null,
  policy: A2AOutboundPolicy,
): Promise<string | null> {
  if (auth.mode === "none") return null;
  if (auth.mode === "bearer") {
    if (!auth.bearerToken) throw new A2ATransportError("Bearer credential outbound peer не настроен", 409);
    return `Bearer ${auth.bearerToken}`;
  }
  if (!subjectToken) throw new A2ATransportError("Delegated outbound вызов требует OIDC access token пользователя", 403);
  if (!auth.tokenUrl || !auth.clientId || !auth.clientSecret) {
    throw new A2ATransportError("OAuth token exchange outbound peer настроен неполностью", 409);
  }
  const parameters = new URLSearchParams({
    grant_type: "urn:ietf:params:oauth:grant-type:token-exchange",
    subject_token: subjectToken,
    subject_token_type: "urn:ietf:params:oauth:token-type:access_token",
    requested_token_type: "urn:ietf:params:oauth:token-type:access_token",
  });
  if (auth.audience) parameters.set("audience", auth.audience);
  if (auth.scopes?.length) parameters.set("scope", auth.scopes.join(" "));
  const clientCredential = Buffer.from(`${auth.clientId}:${auth.clientSecret}`, "utf8").toString("base64");
  const response = await a2aHttpRequest(auth.tokenUrl, {
    method: "POST",
    headers: {
      accept: "application/json",
      authorization: `Basic ${clientCredential}`,
      "content-type": "application/x-www-form-urlencoded",
    },
    body: parameters.toString(),
  }, { ...policy, maxResponseBytes: Math.min(policy.maxResponseBytes, 262_144) });
  const token = parseJsonObject(response, "OAuth token exchange");
  if (typeof token.access_token !== "string" || !token.access_token || token.access_token.length > 32_000) {
    throw new A2ATransportError("OAuth token exchange не вернул bounded access_token", 502, response.status);
  }
  if (token.token_type !== undefined && (typeof token.token_type !== "string" || token.token_type.toLowerCase() !== "bearer")) {
    throw new A2ATransportError("OAuth token exchange вернул неподдерживаемый token_type", 502, response.status);
  }
  return `Bearer ${token.access_token}`;
}

export async function invokeA2ARemote(
  remote: A2ARemoteConnection,
  auth: A2ARemoteAuthMaterial,
  subjectToken: string | null,
  operation: "message:send" | `tasks/${string}` | `tasks/${string}:cancel`,
  body: Record<string, unknown> | null,
  policy: A2AOutboundPolicy,
): Promise<Record<string, unknown>> {
  const authorization = await outboundAuthorization(auth, subjectToken, policy);
  const tenantPrefix = remote.tenant ? `${encodeURIComponent(remote.tenant)}/` : "";
  const effectiveBody = body && remote.tenant ? { ...body, tenant: remote.tenant } : body;
  const response = await a2aHttpRequest(`${remote.interfaceUrl}/${tenantPrefix}${operation}`, {
    method: operation.startsWith("tasks/") && !operation.endsWith(":cancel") ? "GET" : "POST",
    headers: {
      "a2a-version": A2A_PROTOCOL_VERSION,
      ...(effectiveBody ? { "content-type": A2A_MEDIA_TYPE } : {}),
      ...(authorization ? { authorization } : {}),
    },
    ...(effectiveBody ? { body: JSON.stringify(effectiveBody) } : {}),
  }, { ...policy, maxResponseBytes: Math.min(policy.maxResponseBytes, remote.maxResponseBytes) });
  return parseJsonObject(response, `Outbound A2A ${operation}`);
}

export async function sendA2APush(
  url: string,
  authorization: string | null,
  payload: Record<string, unknown>,
  policy: A2AOutboundPolicy,
): Promise<void> {
  const response = await a2aHttpRequest(url, {
    method: "POST",
    allowQuery: true,
    headers: {
      "content-type": A2A_MEDIA_TYPE,
      "a2a-version": A2A_PROTOCOL_VERSION,
      ...(authorization ? { authorization } : {}),
    },
    body: JSON.stringify(payload),
  }, policy);
  if (response.status < 200 || response.status >= 300) {
    throw new A2ATransportError(`Push callback вернул HTTP ${response.status}`, 502, response.status);
  }
}
