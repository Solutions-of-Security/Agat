export const POSTGRES_RESPONSE_HEADER_BYTES = 16;

export class PostgresResponseTimeout extends Error {
  constructor(timeoutMs: number) {
    super(`PostgreSQL state store не ответил за ${timeoutMs} ms`);
  }
}

/** One in-flight synchronous request owns this buffer until its JSON is copied. */
export class PostgresResponseBuffer {
  private readonly shared: SharedArrayBuffer;
  private readonly header: Int32Array;

  constructor(private readonly responseBytes: number) {
    this.shared = new SharedArrayBuffer(POSTGRES_RESPONSE_HEADER_BYTES + responseBytes);
    this.header = new Int32Array(this.shared, 0, POSTGRES_RESPONSE_HEADER_BYTES / Int32Array.BYTES_PER_ELEMENT);
  }

  exchange(send: (shared: SharedArrayBuffer) => void, timeoutMs: number): string {
    Atomics.store(this.header, 1, 0);
    Atomics.store(this.header, 0, 0);
    send(this.shared);
    const deadline = performance.now() + timeoutMs;
    // A notification may arrive after the previous response was already read
    // via not-equal. It is not proof that this request's response is ready.
    while (Atomics.load(this.header, 0) === 0) {
      const remaining = deadline - performance.now();
      if (remaining <= 0) throw new PostgresResponseTimeout(timeoutMs);
      Atomics.wait(this.header, 0, 0, remaining);
    }
    const length = Atomics.load(this.header, 1);
    if (length < 0 || length > this.responseBytes) {
      throw new Error("PostgreSQL state store вернул повреждённый ответ");
    }
    const bytes = new Uint8Array(this.shared, POSTGRES_RESPONSE_HEADER_BYTES, length);
    return Buffer.from(bytes).toString("utf8");
  }
}
