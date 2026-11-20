import os
from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse
from starlette.requests import Request

mcp = FastMCP("MiniMax Safe Gateway", host="0.0.0.0", port=int(os.getenv("PORT","8080")), stateless_http=True, json_response=True)

@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request):
    return JSONResponse({"status":"ok","paid_generation":False})

@mcp.tool()
def capabilities() -> dict:
    """Report gateway status without making any paid API request."""
    return {"api_key_configured":bool(os.getenv("MINIMAX_API_KEY")), "paid_generation":False, "planned":["text_to_speech","voice_cloning","video_generation","image_generation"]}

@mcp.tool()
def preview_speech(text: str, model: str = "speech-02-turbo") -> dict:
    """Preview a speech request locally; never contacts MiniMax."""
    if not text.strip() or len(text)>10000:
        raise ValueError("Text must contain 1 to 10000 characters")
    return {"model":model,"characters":len(text),"utf8_bytes":len(text.encode("utf-8")),"submitted":False,"cost":"Not verified; check current provider pricing"}


async def check_provider():
    import httpx
    key = os.getenv("MINIMAX_API_KEY")
    if not key:
        return {"ok": False, "reason": "missing_key"}
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            r = await client.post("https://api.minimax.io/v1/get_voice", headers={"Authorization": "Bearer " + key}, json={"voice_type":"system"})
        data = r.json()
        code = data.get("base_resp", {}).get("status_code")
        return {"ok": r.is_success and code == 0, "http_status":r.status_code,"provider_code":code if isinstance(code,int) else None,"system_voice_count":len(data.get("system_voice") or [])}
    except Exception:
        return {"ok":False,"reason":"request_failed"}

@mcp.tool()
async def check_minimax_auth() -> dict:
    """Read-only system voice catalog authentication check. No generation."""
    return await check_provider()

async def startup_test():
    import json
    print("MINIMAX_AUTH_TEST " + json.dumps(await check_provider()), flush=True)

if __name__ == "__main__":
    import asyncio
    asyncio.run(startup_test())
    mcp.run(transport="streamable-http")
