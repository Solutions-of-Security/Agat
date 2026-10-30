import { parseArgs } from "node:util";
import { userInfo } from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { loadConfig, type CoordinatorConfig } from "./config.js";
import { AgatStore } from "./database.js";
import { authenticationMode } from "./deployment.js";
import { LocalIdentityProvider } from "./local-identities.js";

/** Run only by an operator with access to the stopped coordinator's authority DB. */
export function localAuthCommand(args: string[], config: CoordinatorConfig = loadConfig()): Record<string, unknown> {
  const [command, ...rest] = args;
  const { values } = parseArgs({ args: rest, options: { username: { type: "string" }, subject: { type: "string" }, "confirm-offline": { type: "string" } }, strict: true });
  if (values["confirm-offline"] !== "COORDINATOR_STOPPED") throw new Error("Остановите coordinator и добавьте --confirm-offline COORDINATOR_STOPPED");
  if (authenticationMode(config) !== "local" || config.stateStoreDriver !== "sqlite") throw new Error("Нужны AGAT_AUTH_MODE=local и SQLite");
  if (command !== "bootstrap" && command !== "recover") throw new Error("Команда: bootstrap --username NAME либо recover --subject EXISTING_ADMIN_SUBJECT");
  if (command === "bootstrap" && (!values.username || values.subject) || command === "recover" && (!values.subject || values.username)) throw new Error("Укажите явный username для bootstrap либо существующий subject для recovery");
  const store = new AgatStore(config.dbPath, { seedDemo: false, credentialsKey: config.credentialsKey, artifactsDir: config.artifactsDir });
  try {
    const operator = userInfo();
    const provider = new LocalIdentityProvider(store.db, undefined, (type, data) => store.recordPlatformEvent(type, "Offline управление локальным доступом", {
      ...data, identityActor: { subject: `os:${operator.uid}:${operator.username}`, source: "local-cli", application: "agat-local-auth" },
    }));
    const result = command === "bootstrap" ? provider.bootstrap(values.username!) : provider.recover(values.subject!);
    return { ...result, notice: "Ключ показан один раз. Сохраните его в менеджере секретов; не добавляйте в git или browser storage." };
  } finally { store.close(); }
}

if (import.meta.url === (process.argv[1] ? pathToFileURL(path.resolve(process.argv[1])).href : "")) {
  try { process.stdout.write(`${JSON.stringify(localAuthCommand(process.argv.slice(2)), null, 2)}\n`); }
  catch (error) { console.error(error instanceof Error ? error.message : "Local auth command failed"); process.exitCode = 1; }
}
