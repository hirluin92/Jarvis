from fastapi import APIRouter, HTTPException, Request


def build_realtime_router() -> APIRouter:
    """The endpoints of the browser voice client. They answer 404 unless real-time voice is on
    (REALTIME_VOICE=1): the sessions object is only set on the app in that case."""
    from pipecat.transports.smallwebrtc.request_handler import (
        SmallWebRTCPatchRequest,
        SmallWebRTCRequest,
    )

    router = APIRouter()

    def sessions(request: Request):
        found = getattr(request.app.state, "realtime", None)
        if found is None:
            raise HTTPException(404, "real-time voice is not enabled")
        return found

    @router.get("/status")
    async def status(request: Request) -> dict:
        sessions(request)
        return {"status": "ready", "transports": ["webrtc"]}

    @router.post("/start")
    async def start(request: Request) -> dict:
        found = sessions(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        return await found.start(body if isinstance(body, dict) else {})

    @router.post("/api/offer")
    async def offer(request: Request, payload: SmallWebRTCRequest):
        return await sessions(request).offer(payload)

    @router.patch("/api/offer")
    async def candidate(request: Request, payload: SmallWebRTCPatchRequest):
        return await sessions(request).candidate(payload)

    return router
