# Base MCP

AgentOS ships a featured preset for **Base MCP** — Coinbase's hosted remote MCP
server that gives an agent a Base Account wallet. It uses the same connection
surface as the Robinhood preset: Streamable HTTP transport, a provider-hosted
OAuth flow, and live tool discovery without a gateway restart.

| Setting | Value |
| --- | --- |
| Endpoint | `https://mcp.base.org` |
| Transport | Streamable HTTP |
| Authorization | OAuth (approved per user in Base Account) |
| Provider docs | `https://docs.base.org/agents` |

## What it exposes

Once authorized, Base MCP gives the agent wallet-scoped tools, including:

- balance and portfolio lookups;
- sending native and ERC-20 tokens to addresses, ENS names, basenames, or
  cb.id names;
- token swaps on supported mainnet chains;
- transaction history browsing;
- personal-message and EIP-712 typed-data signing;
- batched contract calls via `send_calls`;
- paying x402-enabled APIs in USDC.

Base also documents a set of native plugins (for example Uniswap, Aerodrome,
Morpho, and OpenSea). The live authenticated MCP schemas are authoritative for
tool names and parameters.

## OAuth integration status

Base MCP uses OAuth 2.1 (Authorization Code + PKCE) for user authentication.
The integration has been verified against the live endpoint at
`https://mcp.base.org`:

- **Endpoint**: `GET /` returns `401` without a bearer token, confirming that
  OAuth is required.
- **Authorization server metadata**: `/.well-known/oauth-authorization-server`
  returns valid RFC 8414 metadata with PKCE (`S256`), authorization code, and
  refresh token support.
- **Dynamic client registration**: the metadata advertises
  `registration_endpoint` at `https://mcp.base.org/register`; scopes include
  `agent_wallet:transact` and `agent_wallet:escalate`.
- **AgentOS compatibility**: the MCP Streamable HTTP client (`MCPStreamableHTTPClient`)
  uses the MCP SDK's `OAuthClientProvider` which discovers the authorization-server
  metadata automatically and registers the client dynamically. The same code path
  is exercised against the Robinhood MCP integration in production.

> **Note**: A full end-to-end smoke test (registration → authorization →
> token exchange → tool discovery) requires a real Base Account. The metadata
> above confirms that Base MCP speaks the same protocol and OAuth dialect as
> Robinhood, and the well-known endpoints exercise the same `FileOAuthStorage`
> and `OAuthClientProvider` paths covered by the existing gateway and MCP boot
> tests.

## Safety model

Every write action requires explicit user approval inside the Base Account —
the same "approval before value moves" shape as the Robinhood integration, so
the Base preset fits the existing AgentOS approval story rather than widening
it.

> Onchain transactions are irreversible. Review every approval in your Base
> Account, and read
> [`../approvals-and-permissions.md`](../approvals-and-permissions.md) before
> letting any automation move funds.

## Connect from the Web UI

Open **Settings > MCP Servers** and use the **Base** partner card:

1. Select **Connect Base**. The editor opens prefilled with the Base MCP
   endpoint and OAuth enabled.
2. Save the connection. Saving opens the Base Account authorization flow.
3. Approve the connection in your Base Account. The server's tools load
   immediately — no gateway restart required.

## Connect from TOML

For scripted deployments, declare the same server in `config.toml`:

```toml
[mcp]
enabled = true
connect_timeout_seconds = 10

[[mcp.servers]]
name = "base-mcp"
transport = "streamable_http"
url = "https://mcp.base.org"
oauth = true
tool_timeout_seconds = 30
```

OAuth tokens are stored under the AgentOS state directory, not in
`config.toml` — see [`../configuration.md`](../configuration.md) for the
storage details and the full MCP reference.

## Notes

- The bundled onchain skills (for example the `gmgn-*` research skills) gain a
  sanctioned wallet path when Base MCP is connected.
- A bundled `base-onchain` skill is planned as a follow-up; this page covers
  the MCP connection itself.
