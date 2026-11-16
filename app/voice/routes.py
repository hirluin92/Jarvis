from fastapi import APIRouter, HTTPException, Request


def is_remote(request: Request) -> bool:
    """True when the request came through Tailscale Serve (the phone), not from this PC."""
    h = request.headers
    return "tailscale-user-login" in h or "x-forwarded-for" in h


def build_realtime_router() -> APIRouter:
    """The endpoints of the browser voice client. They answer 404 unless real-time voice is on
    (REALTIME_VOICE=1): the sessions object is only set on the app in that case.

    The web client calls `/start`, then sends its WebRTC offer to `/sessions/{sessionId}/api/offer`
    (the session-scoped path the official runner proxies); the plain `/api/offer` is kept too.
    """
    from pipecat.transports.smallwebrtc.request_handler import (
        IceCandidate,
        SmallWebRTCPatchRequest,
        SmallWebRTCRequest,
    )

    router = APIRouter()

    def sessions(request: Request):
        found = getattr(request.app.state, "realtime", None)
        if found is None:
            raise HTTPException(404, "real-time voice is not enabled")
        return found

    async def body(request: Request) -> dict:
        try:
            data = await request.json()
        except Exception as e:
            raise HTTPException(400, "invalid JSON body") from e
        if not isinstance(data, dict):
            raise HTTPException(400, "invalid JSON body")
        return data

    @router.get("/status")
    async def status(request: Request) -> dict:
        sessions(request)
        return {"status": "ready", "transports": ["webrtc"]}

    @router.post("/start")
    async def start(request: Request) -> dict:
        found = sessions(request)
        try:
            data = await request.json()
        except Exception:
            data = {}
        return await found.start(data if isinstance(data, dict) else {})

    @router.post("/api/offer")
    @router.post("/sessions/{session_id}/api/offer")
    async def offer(request: Request, session_id: str | None = None):
        found = sessions(request)
        data = await body(request)
        try:
            payload = SmallWebRTCRequest(
                sdp=data["sdp"],
                type=data["type"],
                pc_id=data.get("pc_id"),
                restart_pc=data.get("restart_pc"),
                request_data=data.get("request_data") or data.get("requestData"),
            )
        except KeyError as e:
            raise HTTPException(400, f"missing field {e}") from e
        return await found.offer(payload, remote=is_remote(request))

    @router.patch("/api/offer")
    @router.patch("/sessions/{session_id}/api/offer")
    async def candidate(request: Request, session_id: str | None = None):
        found = sessions(request)
        data = await body(request)
        try:
            payload = SmallWebRTCPatchRequest(
                pc_id=data["pc_id"],
                candidates=[IceCandidate(**c) for c in data.get("candidates", [])],
            )
        except (KeyError, TypeError) as e:
            raise HTTPException(400, f"invalid candidate request: {e}") from e
        return await found.candidate(payload)

    return router
