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
  const value = header.trim();
  // Keep prefix whitespace separate from the token scan: overlapping greedy
  // groups can backtrack quadratically on a long malformed header.
  const prefix = /^Bearer\s+/i.exec(value);
  if (!prefix) return null;
  const token = value.slice(prefix[0].length);
  return token && !/[\r\n\u2028\u2029]/.test(token) ? token : null;
}
