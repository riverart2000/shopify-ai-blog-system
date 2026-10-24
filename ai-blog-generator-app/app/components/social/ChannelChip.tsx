import { PROVIDER_COLORS, providerLabel } from "./vocab";

export function NetworkDot({ provider }: { provider: string }) {
  const label = providerLabel(provider);
  return (
    <span className="sa-net" style={{ background: PROVIDER_COLORS[provider] || "#616161" }} title={label} aria-hidden="true">
      {label.charAt(0)}
    </span>
  );
}

/** Avatar + network + account name. */
export function ChannelChip({ provider, name, picture }: { provider: string; name?: string; picture?: string }) {
  const network = providerLabel(provider);
  return (
    <span className="sa-chip" title={`${network}${name ? ` · ${name}` : ""}`}>
      {picture ? <img className="sa-avatar" src={picture} alt="" referrerPolicy="no-referrer" /> : <NetworkDot provider={provider} />}
      <span className="sa-chip-label">
        <strong>{network}</strong>
        {name ? <span className="sa-muted"> · {name}</span> : null}
      </span>
    </span>
  );
}
