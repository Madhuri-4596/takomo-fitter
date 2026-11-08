"""The Fitter in Your Pocket: FastAPI app serving the API and the static web app.

Run:  .venv/Scripts/python -m uvicorn app.server:app --port 8765
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import fitting, photo, voice
from .session import Clarify, Session, store

STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="The Fitter in Your Pocket", version="2.0.0")


# ------------------------------------------------------------------ stateless fitting API
class CompareBody(BaseModel):
    answers: dict
    measured: dict


@app.get("/api/demo-golfers")
def demo_golfers():
    return fitting.demo_golfers()


@app.post("/api/recommend")
def recommend(answers: dict):
    try:
        return fitting.recommend(fitting.answers_from_form(answers))
    except fitting.InputError as e:
        raise HTTPException(422, str(e))


@app.post("/api/compare")
def compare(body: CompareBody):
    try:
        return fitting.compare(body.answers, body.measured)
    except fitting.InputError as e:
        raise HTTPException(422, str(e))


@app.post("/api/photo/sheet")
async def photo_sheet(image: UploadFile = File(...)):
    try:
        return photo.find_sheet(photo.decode(await image.read()))
    except photo.PhotoError as e:
        raise HTTPException(422, str(e))


@app.post("/api/photo/measure")
async def photo_measure(image: UploadFile = File(...), points: str = Form(...), corners: str | None = Form(default=None)):
    try:
        p = json.loads(points)
        pts = [tuple(map(float, p[k]))[:2] for k in ("head_top", "wrist", "floor")]
        c = None if not corners else [tuple(map(float, xy))[:2] for xy in json.loads(corners)]
    except (ValueError, KeyError, TypeError):
        raise HTTPException(422, "points must be JSON with head_top, wrist and floor as [x, y] pixel pairs; "
                                 "corners, if given, a list of four [x, y] pairs")
    try:
        return photo.measure(photo.decode(await image.read()), *pts, corners=c)
    except photo.PhotoError as e:
        raise HTTPException(422, str(e))


class HeightScaleBody(BaseModel):
    head_top: list[float] = Field(min_length=2, max_length=2)
    wrist: list[float] = Field(min_length=2, max_length=2)
    floor: list[float] = Field(min_length=2, max_length=2)
    height_cm: float
    footwear: str = Field(default="golf_shoes", pattern="^(golf_shoes|barefoot)$")


@app.post("/api/photo/measure-height")
def photo_measure_height(body: HeightScaleBody):
    """Entered-height scale: only three marker positions are sent, never the photo."""
    try:
        return photo.measure_with_height(body.head_top, body.wrist, body.floor, body.height_cm, body.footwear)
    except photo.PhotoError as e:
        raise HTTPException(422, str(e))


class SwingBody(BaseModel):
    t_ms: list[float] = Field(min_length=1, max_length=5000)
    y: list[float] = Field(min_length=1, max_length=5000)
    visibility: list[float] | None = Field(default=None, max_length=5000)
    time_scale: float = Field(default=1.0, gt=0, le=16)
    answers: dict | None = None            # optional: the current answers, to show the tempo's effect on the shaft
    # optional, for the EXPERIMENTAL clubhead-speed range: hand x positions, shoulder-to-ankle length (px), height
    x: list[float] | None = Field(default=None, max_length=5000)
    body_px: float | None = Field(default=None, gt=0, le=10000)
    height_cm: float | None = Field(default=None, ge=120, le=220)


@app.post("/api/swing/tempo")
def swing_tempo(body: SwingBody):
    """Hand-height track from the browser (no video is uploaded) -> the kit's tempo analysis."""
    from . import swing
    from . import evidence
    try:
        r = swing.analyse(body.t_ms, body.y, body.visibility, body.time_scale, body.x, body.body_px, body.height_cm)
    except swing.SwingError as e:
        raise HTTPException(422, str(e))
    r["tempo_effect"] = evidence.tempo_effect(body.answers, r["tempo"]) if body.answers else None
    return r



class SwingEvents(BaseModel):
    takeaway_s: float
    top_s: float
    impact_s: float
    fps: float = Field(default=30, gt=0, le=480)
    answers: dict | None = None


class OneSwing(BaseModel):
    backswing_s: float
    downswing_s: float
    fps: float = Field(default=30, gt=0, le=480)
    adjusted: bool = False
    hand_mph: float | None = Field(default=None, ge=0, le=60)     # experimental only, never used by the fit


class SwingSet(BaseModel):
    swings: list[OneSwing] = Field(min_length=1, max_length=5)
    answers: dict | None = None


@app.post("/api/swing/events")
def swing_events(body: SwingEvents):
    """Takeaway, top and impact placed or corrected by the golfer on their own video -> tempo by the kit's rule."""
    from . import swing, evidence
    try:
        r = swing.from_events(body.takeaway_s, body.top_s, body.impact_s, body.fps)
    except swing.SwingError as e:
        raise HTTPException(422, str(e))
    r["tempo_effect"] = evidence.tempo_effect(body.answers, r["tempo"]) if body.answers else None
    return r


@app.post("/api/swing/combine")
def swing_combine(body: SwingSet):
    from . import swing, evidence
    try:
        r = swing.combine([x.model_dump() for x in body.swings])
    except swing.SwingError as e:
        raise HTTPException(422, str(e))
    r["tempo_effect"] = evidence.tempo_effect(body.answers, r["tempo"]) if body.answers else None
    return r


class BallLinePoints(BaseModel):
    line_bottom: list[float] = Field(min_length=2, max_length=2)
    line_top: list[float] = Field(min_length=2, max_length=2)
    heel: list[float] = Field(min_length=2, max_length=2)
    toe: list[float] = Field(min_length=2, max_length=2)


class BallLineSet(BaseModel):
    tilts: list[float] | None = Field(default=None, max_length=10)


@app.post("/api/ball-line/tilt")
def ball_line_tilt(body: BallLinePoints):
    """Four points tapped on the face photo (the photo stays on the phone) -> tilt by the kit's camera/ball_line.py."""
    from . import ball_line
    try:
        return {"tilt_deg": ball_line.tilt(body.line_bottom, body.line_top, body.heel, body.toe)}
    except ball_line.BallLineError as e:
        raise HTTPException(422, str(e))


class StrikeBody(BaseModel):
    marks: list[list[float]] = Field(max_length=20)
    heel: list[float] = Field(min_length=2, max_length=2)
    toe: list[float] = Field(min_length=2, max_length=2)


@app.post("/api/strike/detect")
async def strike_detect(image: UploadFile = File(...), heel: str = Form(...), toe: str = Form(...)):
    from . import evidence
    try:
        h, t = json.loads(heel), json.loads(toe)
        return {"marks": evidence.detect_marks(photo.decode(await image.read()), h, t)}
    except (ValueError, TypeError) as e:
        raise HTTPException(422, str(e))


@app.post("/api/strike/analyse")
def strike_analyse(body: StrikeBody):
    from . import evidence
    try:
        return evidence.analyse_marks(body.marks, body.heel, body.toe)
    except evidence.StrikeError as e:
        raise HTTPException(422, str(e))


@app.get("/api/photo/thresholds")
def photo_thresholds():
    return {"prototype": True, "note": "Engineering guesses checked on synthetic images only, not validated error limits.",
            "thresholds": photo.THRESHOLDS, "assumed_method_error_cm": photo.METHOD_ERROR_CM}


FAVICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="12" fill="#0B0B0B"/>'
           '<path d="M14 16h36v9H37v25h-10V25H14z" fill="#FF6E01"/></svg>')


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """The browser's tab icon (it asks for this on every page): Takomo orange on Midnight Black."""
    return Response(FAVICON, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/measurement-sheet.pdf")
def sheet_pdf():
    return FileResponse(fitting.KIT / "camera" / "measurement_sheet_A4.pdf", media_type="application/pdf",
                        filename="takomo_measurement_sheet_A4.pdf")


# ------------------------------------------------------------------ fitting session (shared by form and voice)
def current(request: Request, x_session_token: str | None = Header(default=None)):
    s = store.get(x_session_token)
    if not s:
        raise HTTPException(401, "Session expired. Reload the page to start again.")
    yield s
    # only requests that change something save it back: with several servers (Vercel), a read that started a moment
    # before a change would otherwise write its older copy over the change
    if request.method != "GET":
        store.save(s)


def view(s: Session) -> dict:
    return {**s.state(), "full_recommendation": s.full_recommendation()}


class ProfileBody(BaseModel):
    values: dict


class Update(BaseModel):
    field: str
    value: str | int | float
    unit: str | None = None
    kind: str | None = None
    heard_text: str | None = None


class ProposeBody(BaseModel):
    updates: list[Update] = Field(min_length=1, max_length=12)


class ConfirmBody(BaseModel):
    keys: list[str]
    edits: dict[str, dict] | None = None


class MeasurementBody(BaseModel):
    height_cm: float
    wrist_to_floor_cm: float
    source: str = Field(pattern="^(photo|tape|simulated)$")   # photo: detail.calibration says which method
    detail: dict | None = None


@app.post("/api/session")
def new_session():
    return {"session_id": store.create().id}


@app.get("/api/session")
def get_session(s: Session = Depends(current)):
    return view(s)


@app.put("/api/session/profile")
def put_profile(body: ProfileBody, s: Session = Depends(current)):
    s.set_profile(body.values)
    return view(s)


@app.post("/api/session/propose")
def propose(body: ProposeBody, s: Session = Depends(current)):
    results = s.propose([u.model_dump() for u in body.updates])
    return {"results": results, "state": view(s)}


@app.post("/api/session/confirm")
def confirm(body: ConfirmBody, s: Session = Depends(current)):
    try:
        done = s.confirm_staged(body.keys, body.edits)
    except Clarify as c:
        raise HTTPException(422, str(c))
    return {"confirmed": done, "state": view(s)}


@app.post("/api/session/discard")
def discard(body: ConfirmBody, s: Session = Depends(current)):
    s.discard_staged(body.keys)
    return view(s)


@app.post("/api/session/measurement")
def stage_measurement(body: MeasurementBody, s: Session = Depends(current)):
    try:
        s.stage_measurement(body.height_cm, body.wrist_to_floor_cm, body.source, body.detail)
    except Clarify as c:
        raise HTTPException(422, str(c))
    return view(s)


@app.post("/api/session/measurement/confirm")
def confirm_measurement(s: Session = Depends(current)):
    try:
        s.confirm_measurement()
    except Clarify as c:
        raise HTTPException(409, str(c))
    return view(s)


@app.post("/api/session/recommendation")
def session_recommendation(s: Session = Depends(current)):
    out = s.recommend()
    return {"tool_result": out, "state": view(s)}


# ------------------------------------------------------------------ voice
@app.get("/api/voice/config")
def voice_config():
    if not voice.configured():
        return {"configured": False}
    try:
        return {"configured": True, "privacy": voice.privacy()}
    except voice.VoiceError as e:
        return {"configured": True, "privacy": None, "warning": str(e)}


@app.post("/api/voice/signed-url")
def voice_signed_url(s: Session = Depends(current)):
    try:
        return {"signed_url": voice.signed_url(), "fit_tool_mode": os.environ.get("FIT_TOOL_MODE", "client")}
    except voice.VoiceError as e:
        raise HTTPException(e.status, str(e))


class VoiceSessionBody(BaseModel):
    mode: str = Field(pattern="^(voice|text)$")


@app.post("/api/voice/session")
def voice_session(body: VoiceSessionBody, s: Session = Depends(current)):
    """Voice: a WebRTC conversation token. Typing: a WebSocket signed URL. The API key never leaves the server."""
    try:
        if body.mode == "voice":
            return {"connection_type": "webrtc", "conversation_token": voice.conversation_token()}
        return {"connection_type": "websocket", "signed_url": voice.signed_url()}
    except voice.VoiceError as e:
        raise HTTPException(e.status, str(e))


@app.post("/api/agent/fit")
def agent_fit(authorization: str | None = Header(default=None),
              x_fitting_session: str | None = Header(default=None)):
    """Webhook tool get_fit_recommendation: authenticated server-to-server call from ElevenLabs."""
    if not voice.check_tool_secret(authorization):
        raise HTTPException(401, "unauthorised")
    s = store.get(x_fitting_session)
    if not s:
        return {"ok": False, "error": "session_not_found",
                "say": "I've lost track of your fitting session. Ask the golfer to reload the page."}
    out = s.recommend()
    store.save(s)
    return out


@app.post("/api/session/ball-line")
def save_ball_line(body: BallLineSet, s: Session = Depends(current)):
    """Golfer-confirmed ball-line shots (a real tap in the web app); null clears the test. The lie then comes from them."""
    from . import ball_line
    try:
        s.set_ball_line(body.tilts)
        if s.recommendation and not s.missing():
            s.recommend()
    except (ball_line.BallLineError, Clarify) as e:
        raise HTTPException(422, str(e))
    return view(s)


@app.post("/api/session/strike")
def save_strike(body: StrikeBody, s: Session = Depends(current)):
    """Golfer-confirmed marks only (the web app calls this from a real tap)."""
    from . import evidence
    try:
        s.set_strike(evidence.analyse_marks(body.marks, body.heel, body.toe))
    except evidence.StrikeError as e:
        raise HTTPException(422, str(e))
    return view(s)


@app.post("/api/session/swings")
def save_swings(body: SwingSet, s: Session = Depends(current)):
    """Saves up to three accepted swings (a real tap in the web app) as the tempo evidence. The tempo answer is separate."""
    from . import swing
    try:
        s.set_swing(swing.combine([x.model_dump() for x in body.swings]))
    except swing.SwingError as e:
        raise HTTPException(422, str(e))
    return view(s)


@app.post("/api/session/swing")
def save_swing(body: SwingBody, s: Session = Depends(current)):
    """Saves the swing-tempo evidence (from a real tap in the web app). The tempo answer is changed separately."""
    from . import swing
    try:
        s.set_swing(swing.analyse(body.t_ms, body.y, body.visibility, body.time_scale))
    except swing.SwingError as e:
        raise HTTPException(422, str(e))
    return view(s)


@app.delete("/api/session/strike")
def delete_strike(s: Session = Depends(current)):
    s.set_strike(None)
    return view(s)



class SpeakBody(BaseModel):
    text: str = Field(min_length=1, max_length=500)


@app.post("/api/tts")
def tts_line(body: SpeakBody):
    """One line of the fitter's speech, in a natural ElevenLabs voice, with word timings for the caption."""
    from . import tts
    try:
        return tts.speak(body.text)
    except tts.TTSError as e:
        raise HTTPException(503, str(e))


@app.get("/api/lie-neutral")
def lie_neutral(height_cm: float):
    """Wrist-to-floor that gives a standard (0) static lie at this height: used when the golfer skips the tape,
    so the lie is 'standard, a guess' as the fitter frontend's design says, not an invented measurement."""
    if not 120 <= height_cm <= 220:
        raise HTTPException(422, "height out of range")
    lo, hi = 55.0, 120.0
    for _ in range(40):                          # the static reading rises with wrist-to-floor
        mid = (lo + hi) / 2
        if fitting.lie_detail(height_cm, mid)["raw"] > 0:
            hi = mid
        else:
            lo = mid
    return {"wrist_to_floor_cm": round((lo + hi) / 2, 1)}


@app.get("/")
def fitter_page():
    """The fitter frontend (prototypes/fitter.html from feature/ai-fitter-prototype), connected to this backend."""
    return FileResponse(STATIC / "fitter" / "fitter.html")


@app.get("/classic")
def classic_page():
    """The earlier single-team UI, kept as a fallback and for its browser tests."""
    return FileResponse(STATIC / "index.html")


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
