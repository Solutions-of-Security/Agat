export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <a className="brand" href="#overview" aria-label="АГАТ — на обзор">
      <svg className="brand__mark" viewBox="0 0 48 48" aria-hidden="true">
        <path d="M24 3 43 14v20L24 45 5 34V14L24 3Zm0 8.2-11.9 6.9v13.8L24 38.8l11.9-6.9V18.1L24 11.2Z" />
        <path className="brand__mark-core" d="m24 17 6 3.5v7L24 31l-6-3.5v-7L24 17Z" />
      </svg>
      {compact ? null : <span>АГАТ</span>}
    </a>
  );
}
