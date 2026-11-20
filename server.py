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


import hashlib
import hmac
import json
from decimal import Decimal
import sqlite3
import httpx
from starlette.responses import Response

class GatewayAuth:
    def __init__(self, app):
        self.app = app
    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] != "/health":
            token = os.getenv("MCP_ACCESS_TOKEN", "")
            supplied = dict(scope.get("headers", [])).get(b"authorization", b"")
            if len(token) < 32 or not hmac.compare_digest(supplied, ("Bearer " + token).encode()):
                return await Response("Unauthorized", status_code=401)(scope, receive, send)
        await self.app(scope, receive, send)

def plan(kind, payload, estimate):
    digest = hashlib.sha256(json.dumps([kind,payload],sort_keys=True,separators=(",",":")).encode()).hexdigest()
    return {"request_id":digest,"estimated_usd":str(estimate),"submitted":False,"payload":payload}

def speech_plan(text, voice_id):
    if not text.strip() or len(text)>1000 or not voice_id.strip():
        raise ValueError("Expected 1–1000 characters and a voice ID")
    return plan("speech",{"model":"speech-2.8-turbo","text":text,"stream":False,"voice_setting":{"voice_id":voice_id},"audio_setting":{"format":"mp3"},"output_format":"url"},Decimal(len(text.encode("utf-8")))*Decimal("0.00006"))

def clone_plan(file_id, voice_id):
    if file_id <= 0 or not voice_id.strip():
        raise ValueError("Existing uploaded file ID and voice ID required")
    return plan("clone",{"file_id":file_id,"voice_id":voice_id},Decimal("1.50"))

def video_plan(prompt):
    if not prompt.strip() or len(prompt)>2000:
        raise ValueError("Expected 1–2000 characters")
    return plan("video",{"model":"MiniMax-Hailuo-02","prompt":prompt,"duration":6,"resolution":"768P"},Decimal("0.80"))

def reserve(p):
    if os.getenv("ENABLE_PAID_GENERATION") != "true":
        raise ValueError("Paid generation disabled")
    if not hmac.compare_digest(os.getenv("APPROVED_REQUEST_ID",""),p["request_id"]):
        raise ValueError("Request has not been approved by operator")
    limit = Decimal(os.getenv("APPROVED_MAX_USD","0"))
    if not limit.is_finite() or limit <= 0 or Decimal(p["estimated_usd"]) > limit:
        raise ValueError("Budget insufficient")
    # Require an operator-provisioned persistent Railway volume.
    if not os.path.ismount("/data"):
        raise ValueError("Persistent spending ledger unavailable")
    with sqlite3.connect("/data/spend.sqlite") as db:
        db.execute("CREATE TABLE IF NOT EXISTS reservations (id TEXT PRIMARY KEY, estimate TEXT NOT NULL)")
        try:
            db.execute("INSERT INTO reservations VALUES (?,?)",(p["request_id"],p["estimated_usd"]))
        except sqlite3.IntegrityError:
            raise ValueError("Request already reserved; automatic retries prohibited")

async def submit(p, endpoint):
    reserve(p)
    try:
        async with httpx.AsyncClient(timeout=90,follow_redirects=False) as client:
            r = await client.post("https://api.minimax.io"+endpoint,headers={"Authorization":"Bearer "+os.environ["MINIMAX_API_KEY"]},json=p["payload"])
        result = r.json()
        return {"http_status":r.status_code,"provider_code":result.get("base_resp",{}).get("status_code"),"task_id":result.get("task_id"),"audio":result.get("data",{}).get("audio"),"request_id":p["request_id"]}
    except Exception:
        return {"request_id":p["request_id"],"status":"unknown; do not retry automatically"}

@mcp.tool()
def quote_speech(text: str, voice_id: str) -> dict:
    """Free local estimate; UTF-8 bytes used conservatively."""
    return speech_plan(text,voice_id)

@mcp.tool()
def quote_voice_clone(file_id: int, voice_id: str) -> dict:
    """Free estimate, existing uploaded file only; no upload."""
    return clone_plan(file_id,voice_id)

@mcp.tool()
def quote_video(prompt: str) -> dict:
    """Free preliminary estimate; video rate not verified, submission disabled."""
    p = video_plan(prompt)
    p["estimated_usd"] = "unverified"
    return p

@mcp.tool()
async def generate_speech(text: str, voice_id: str) -> dict:
    """Chargeable; requires explicit operator approval and persistent ledger."""
    return await submit(speech_plan(text,voice_id),"/v1/t2a_v2")

@mcp.tool()
async def clone_voice(file_id: int, voice_id: str) -> dict:
    """Chargeable; only use an audio file with consent and operator approval."""
    return await submit(clone_plan(file_id,voice_id),"/v1/voice_clone")

@mcp.tool()
def generate_video(prompt: str) -> dict:
    """Fail closed until current provider model pricing and schema verified."""
    return {"submitted":False,"reason":"Video pricing and request schema require verification","quote":quote_video(prompt)}

def local_tests():
    p = speech_plan("Hello","test")
    assert p["estimated_usd"] == "0.00030"
    assert p["request_id"] == speech_plan("Hello","test")["request_id"]
    assert p["request_id"] != speech_plan("Hello!","test")["request_id"]
    if os.getenv("ENABLE_PAID_GENERATION") != "true":
        try:
            reserve(p)
        except ValueError as e:
            assert str(e) == "Paid generation disabled"
        else:
            raise AssertionError("Paid gate failed")
    print("LOCAL_SAFETY_TEST passed",flush=True)

if __name__ == "__main__":
    import asyncio
    asyncio.run(startup_test())
    local_tests()
    import uvicorn
    uvicorn.run(GatewayAuth(mcp.streamable_http_app()),host="0.0.0.0",port=int(os.getenv("PORT","8080")))
