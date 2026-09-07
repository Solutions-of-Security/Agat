export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <a className="brand" href="#overview" aria-label="АГАТ — на обзор">
      <img className="brand__mark" src="/brand/agat-mark.png" width="38" height="38" alt="" />
      {compact ? null : <span>АГАТ</span>}
    </a>
  );
}
