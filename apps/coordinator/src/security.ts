import { createHash, randomBytes, timingSafeEqual } from "node:crypto";

export function hashToken(token: string): string {
  return createHash("sha256").update(token, "utf8").digest("hex");
}

export function createToken(bytes = 32): string {
  return randomBytes(bytes).toString("base64url");
}

export function tokensEqual(left: string, right: string): boolean {
  const leftHash = Buffer.from(hashToken(left), "hex");
  const rightHash = Buffer.from(hashToken(right), "hex");
  return timingSafeEqual(leftHash, rightHash);
}

export function bearerToken(header: string | undefined): string | null {
  if (!header) return null;
  const match = /^Bearer\s+(.+)$/i.exec(header.trim());
  return match?.[1] ?? null;
}
