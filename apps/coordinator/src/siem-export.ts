export interface SiemResponseDecision {
  delivered: boolean;
  terminal: boolean;
  reasonCode: "ack_missing" | "ack_mismatch" | "http_4xx" | "http_5xx" | null;
  errorMessage: string | null;
}

export function evaluateSiemResponse(
  status: number,
  acknowledgement: string,
  expectedAcknowledgement: string,
  requireAcknowledgement: boolean,
): SiemResponseDecision {
  if (status >= 200 && status < 300) {
    if (!requireAcknowledgement || acknowledgement === expectedAcknowledgement) {
      return { delivered: true, terminal: false, reasonCode: null, errorMessage: null };
    }
    return {
      delivered: false,
      terminal: false,
      reasonCode: acknowledgement ? "ack_mismatch" : "ack_missing",
      errorMessage: acknowledgement
        ? "SIEM batch acknowledgement не совпадает"
        : "SIEM batch acknowledgement отсутствует",
    };
  }
  const retryable = status === 408
    || status === 409
    || status === 425
    || status === 429
    || status >= 500;
  return {
    delivered: false,
    terminal: !retryable,
    reasonCode: status >= 500 ? "http_5xx" : "http_4xx",
    errorMessage: `SIEM ответил HTTP ${status}`,
  };
}
