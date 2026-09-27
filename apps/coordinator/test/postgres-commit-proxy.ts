import assert from "node:assert/strict";
import net from "node:net";

/** Loopback-only fault fixture: forward COMMIT, retain its real server reply.
 * Only the named retrieval connection, after INSERT knowledge_retrievals, is
 * affected. Authentication bytes and SQL payloads are never logged or saved.
 */
export async function interceptRetrievalCommit(connectionString: string, applicationName: string) {
  const destination = new URL(connectionString);
  assert.equal(destination.hostname, "127.0.0.1");
  assert.ok(Number(destination.port) > 0);
  const sockets = new Set<net.Socket>();
  const errors: Error[] = [];
  let fired = false, completed = false;
  let held: { client: net.Socket; upstream: net.Socket } | undefined;
  let confirm!: () => void;
  const committed = new Promise<void>(resolve => { confirm = resolve; });
  const server = net.createServer(client => {
    const upstream = net.connect({ host: "127.0.0.1", port: Number(destination.port) });
    for (const socket of [client, upstream]) {
      // Match pg's transport: individual protocol messages must not acquire
      // Nagle/delayed-ACK latency before the intended COMMIT fault.
      socket.setNoDelay(true);
      sockets.add(socket); socket.on("close", () => sockets.delete(socket));
      socket.on("error", error => { errors.push(error); client.destroy(); upstream.destroy(); });
    }
    client.on("close", () => upstream.destroy()); upstream.on("close", () => client.destroy());
    let input: Buffer = Buffer.alloc(0), output: Buffer = Buffer.alloc(0);
    let startup = true, target = false, wroteRetrieval = false, withholding = false;
    const broken = (error: unknown) => { errors.push(error as Error); client.destroy(); upstream.destroy(); };
    client.on("data", chunk => {
      try {
        assert.ok(Buffer.isBuffer(chunk));
        input = Buffer.concat([input, chunk]); assert.ok(input.length <= 4 * 1024 * 1024);
        while (input.length >= (startup ? 4 : 5)) {
          const size = startup ? input.readInt32BE(0) : input.readInt32BE(1) + 1;
          assert.ok(size >= (startup ? 8 : 5) && size <= 4 * 1024 * 1024);
          if (input.length < size) break;
          const packet = input.subarray(0, size); input = input.subarray(size);
          if (startup) {
            assert.equal(packet.readInt32BE(4), 196608, "The test fixture requires plaintext PostgreSQL v3 on loopback");
            const fields = packet.subarray(8).toString("utf8").split("\0");
            for (let i = 0; i + 1 < fields.length; i += 2) if (fields[i] === "application_name") target = fields[i + 1] === applicationName;
            startup = false;
          } else if (target) {
            const type = String.fromCharCode(packet[0]!);
            const body = packet.subarray(5);
            const query = type === "Q" ? body.toString("utf8").replace(/\0$/, "").trim()
              : type === "P" ? body.subarray(body.indexOf(0) + 1).toString("utf8").split("\0")[0]! : "";
            if (/^INSERT INTO knowledge_retrievals\s*\(/i.test(query.trim())) wroteRetrieval = true;
            if (wroteRetrieval && /^COMMIT$/i.test(query) && !fired) {
              fired = true; withholding = true; held = { client, upstream };
            }
          }
          upstream.write(packet);
        }
      } catch (error) { broken(error); }
    });
    upstream.on("data", chunk => {
      try {
        assert.ok(Buffer.isBuffer(chunk));
        output = Buffer.concat([output, chunk]); assert.ok(output.length <= 4 * 1024 * 1024);
        while (output.length >= 5) {
          const size = output.readInt32BE(1) + 1; assert.ok(size >= 5 && size <= 4 * 1024 * 1024);
          if (output.length < size) break;
          const packet = output.subarray(0, size); output = output.subarray(size);
          if (!withholding) client.write(packet);
          else if (packet[0] === 67 && packet.subarray(5).toString("utf8") === "COMMIT\0") { completed = true; confirm(); }
        }
      } catch (error) { broken(error); }
    });
  });
  await new Promise<void>((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
  const address = server.address(); assert.ok(address && typeof address === "object");
  return { committed, errors,
    route: (value: string) => {
      const url = new URL(value); assert.equal(url.hostname, destination.hostname); assert.equal(url.port, destination.port);
      url.port = String(address.port); return url.toString();
    },
    disconnect: () => { assert.ok(completed && held, "No successful COMMIT reply was intercepted"); held.client.destroy(); held.upstream.destroy(); },
    close: async () => {
      for (const socket of sockets) socket.destroy();
      await new Promise<void>(resolve => server.close(() => resolve()));
    } };
}
