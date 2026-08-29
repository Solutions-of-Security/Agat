import { createCipheriv, createDecipheriv, createHash, randomBytes } from "node:crypto";

const VERSION = "v1";

function encryptionKey(secret: string): Buffer {
  if (!secret.trim()) throw new Error("Ключ шифрования credentials не настроен");
  return createHash("sha256").update(secret, "utf8").digest();
}

export function encryptCredential(data: Record<string, string>, secret: string): string {
  const iv = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", encryptionKey(secret), iv);
  const plaintext = Buffer.from(JSON.stringify(data), "utf8");
  const encrypted = Buffer.concat([cipher.update(plaintext), cipher.final()]);
  const tag = cipher.getAuthTag();
  return [VERSION, iv.toString("base64url"), tag.toString("base64url"), encrypted.toString("base64url")].join(".");
}

export function decryptCredential(payload: string, secret: string): Record<string, string> {
  const [version, ivValue, tagValue, encryptedValue, extra] = payload.split(".");
  if (version !== VERSION || !ivValue || !tagValue || !encryptedValue || extra) {
    throw new Error("Формат зашифрованных credentials не поддерживается");
  }
  try {
    const decipher = createDecipheriv("aes-256-gcm", encryptionKey(secret), Buffer.from(ivValue, "base64url"));
    decipher.setAuthTag(Buffer.from(tagValue, "base64url"));
    const decoded = Buffer.concat([
      decipher.update(Buffer.from(encryptedValue, "base64url")),
      decipher.final(),
    ]).toString("utf8");
    const value = JSON.parse(decoded) as unknown;
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Некорректные credentials");
    return Object.fromEntries(Object.entries(value).map(([key, field]) => [key, String(field)]));
  } catch (error) {
    if (error instanceof Error && error.message === "Некорректные credentials") throw error;
    throw new Error("Не удалось расшифровать credentials: проверьте AGAT_CREDENTIALS_KEY");
  }
}
