# Nginx Proxy Manager (NPM) setup for mcp.fabrici.xyz

If `ha.fabrici.xyz` already runs through Nginx Proxy Manager, add the MCP host
the same way:

1. **Hosts → Proxy Hosts → Add Proxy Host**
   - Domain Names: `mcp.fabrici.xyz`
   - Scheme: `http`
   - Forward Hostname / IP: the homelab-mcp container's LAN IP
   - Forward Port: `8787`
   - Enable **Block Common Exploits**
   - Enable **Websockets Support**
2. **SSL** tab: request a Let's Encrypt certificate, Force SSL, HTTP/2.
3. **Advanced** tab — paste, so streaming MCP responses are not buffered:

   ```
   proxy_buffering off;
   proxy_read_timeout 3600s;
   proxy_send_timeout 3600s;
   proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
   ```

The app itself still requires `Authorization: Bearer <MCP_AUTH_TOKEN>`, so the
endpoint is not open even though NPM terminates TLS. In `homelab.yaml` set
`server.trusted_proxies` to include the NPM host's IP so the app trusts its
`X-Forwarded-For` for the optional IP allowlist.
