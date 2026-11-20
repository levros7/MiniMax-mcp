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

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
