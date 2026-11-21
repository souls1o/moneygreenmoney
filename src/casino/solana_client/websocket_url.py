from __future__ import annotations


def websocket_url_from_rpc(rpc_url: str) -> str:
    """Turn an HTTP(S) Solana RPC URL (including Helius `?api-key=`) into a WebSocket URL."""
    raw = rpc_url.strip()
    if raw.startswith("https://"):
        return "wss://" + raw[8:]
    if raw.startswith("http://"):
        return "ws://" + raw[7:]
    if raw.startswith("wss://") or raw.startswith("ws://"):
        return raw
    return "wss://" + raw
