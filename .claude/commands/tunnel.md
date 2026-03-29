Check the tunnel status:
1. Read TUNNEL_MODE from ~/wingmen/orchestrator/.env
2. If cloudflared: check `cloudflared tunnel list` and `cloudflared tunnel info wingmen-orch`
3. If ngrok: check `curl -s localhost:4040/api/tunnels` for active tunnels
4. Report the public URL and connection status