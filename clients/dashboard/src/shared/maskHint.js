// Mirrors the server's masking (services/chat-agent/env_config.py,
// services/mcp-server/internal/tools/registry.go) for the optimistic local
// update right after a successful save — never used to display a value that
// didn't just come from the user's own input.
export function maskHint(value) {
  if (!value) return undefined;
  return value.length <= 4 ? `…${value}` : `…${value.slice(-4)}`;
}
