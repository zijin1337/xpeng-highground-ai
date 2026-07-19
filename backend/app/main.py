from __future__ import annotations

import hashlib
import hmac
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, HTTPException, Query, Response, Security, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles

from .actuator import build_actuator
from .config import REPO_ROOT, Settings
from .database import (
    Database,
    EventSupersededError,
    MessageIdConflictError,
    StoredEvent,
)
from .decision_engine import evaluate_decision
from .models import (
    AuthorizationRequest,
    AuthorizationResponse,
    CommandResponse,
    DecisionCode,
    EventDetail,
    HealthResponse,
    MigrationCommandRequest,
    TelemetryDecisionResponse,
    TelemetryIn,
)


API_PREFIX = "/api/v1"
LATEST_RESPONSE_HEADERS = {"Cache-Control": "private, no-store"}


def _event_response(event: StoredEvent) -> TelemetryDecisionResponse:
    return TelemetryDecisionResponse(
        event_id=event.event_id,
        message_id=event.message_id,
        duplicate=event.duplicate,
        received_at=event.received_at,
        input_sha256=event.input_sha256,
        result=event.result,
    )


def _event_detail(event: StoredEvent) -> EventDetail:
    return EventDetail(
        event_id=event.event_id,
        message_id=event.message_id,
        received_at=event.received_at,
        input_sha256=event.input_sha256,
        telemetry=event.telemetry,
        result=event.result,
    )


def _event_is_stale(
    event: StoredEvent,
    max_age_seconds: int,
    *,
    now: datetime | None = None,
) -> bool:
    current_time = now or datetime.now(timezone.utc)
    return (current_time - event.received_at).total_seconds() > max_age_seconds


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.validate()
    database = Database(settings.database_path)
    actuator = build_actuator(settings.actuator_mode)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        database.initialize()
        yield

    app = FastAPI(
        title="XPENG HighGround API",
        summary="暴雨内涝车辆安全位移的可运行决策与证据服务",
        description=(
            "接收真实传感器遥测、持久化原始输入和决策证据、生成 Go/No-Go，"
            "并执行事件级单次授权校验。默认适配器只留痕，不向真实车辆发送指令。"
        ),
        version="1.2.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.database = database
    app.state.actuator = actuator

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-API-Key"],
    )

    api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

    def require_api_key(
        provided: str | None = Security(api_key_header),
    ) -> str:
        if provided is None or not hmac.compare_digest(provided, settings.api_key):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or invalid X-API-Key",
            )
        return provided

    @app.get("/healthz", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        if not database.health():
            raise HTTPException(status_code=503, detail="Database unavailable")
        return HealthResponse(status="ok", database="ok", actuator_mode=settings.actuator_mode)

    @app.get(f"{API_PREFIX}/policy", tags=["decision"])
    def get_policy() -> dict[str, float | int]:
        return settings.policy.to_dict()

    @app.get(f"{API_PREFIX}/session", tags=["system"])
    def session(_: str = Security(require_api_key)) -> dict[str, str]:
        return {
            "status": "authenticated",
            "storage": "sqlite",
            "actuator_mode": settings.actuator_mode,
            "environment": settings.environment,
        }

    @app.post(
        f"{API_PREFIX}/telemetry",
        response_model=TelemetryDecisionResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["telemetry"],
    )
    def ingest_telemetry(
        payload: TelemetryIn,
        response: Response,
        _: str = Security(require_api_key),
    ) -> TelemetryDecisionResponse:
        result = evaluate_decision(payload, settings.policy)
        try:
            event = database.save_telemetry_and_decision(payload, result)
        except MessageIdConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if event.duplicate:
            response.status_code = status.HTTP_200_OK
        return _event_response(event)

    @app.get(
        f"{API_PREFIX}/decisions/latest",
        response_model=EventDetail,
        tags=["decision"],
    )
    def latest_decision(
        response: Response,
        site_id: str = Query(min_length=1, max_length=80),
        vehicle_id: str = Query(min_length=1, max_length=80),
        _: str = Security(require_api_key),
    ) -> EventDetail:
        event = database.get_latest(site_id, vehicle_id)
        if not event:
            raise HTTPException(
                status_code=404,
                detail="No decision found",
                headers=LATEST_RESPONSE_HEADERS,
            )
        if _event_is_stale(event, settings.event_max_age_seconds):
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="Latest decision is stale; ingest fresh telemetry",
                headers=LATEST_RESPONSE_HEADERS,
            )
        response.headers.update(LATEST_RESPONSE_HEADERS)
        return _event_detail(event)

    @app.get(
        f"{API_PREFIX}/events",
        response_model=list[EventDetail],
        tags=["decision"],
    )
    def list_events(
        site_id: str = Query(min_length=1, max_length=80),
        vehicle_id: str = Query(min_length=1, max_length=80),
        limit: int = Query(default=20, ge=1, le=100),
        _: str = Security(require_api_key),
    ) -> list[EventDetail]:
        return [
            _event_detail(event)
            for event in database.list_events(site_id, vehicle_id, limit)
        ]

    @app.get(
        f"{API_PREFIX}/events/{{event_id}}",
        response_model=EventDetail,
        tags=["decision"],
    )
    def get_event(event_id: str, _: str = Security(require_api_key)) -> EventDetail:
        event = database.get_event(event_id)
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")
        return _event_detail(event)

    @app.post(
        f"{API_PREFIX}/authorizations",
        response_model=AuthorizationResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["authorization"],
    )
    def authorize_event(
        request: AuthorizationRequest,
        _: str = Security(require_api_key),
    ) -> AuthorizationResponse:
        event = database.get_event(request.event_id)
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")
        if event.result.decision != DecisionCode.MIGRATE_NOW:
            raise HTTPException(status_code=409, detail="Event is not eligible for migration")

        now = datetime.now(timezone.utc)
        if _event_is_stale(event, settings.event_max_age_seconds, now=now):
            raise HTTPException(status_code=409, detail="Event is stale; ingest fresh telemetry")

        token = secrets.token_urlsafe(32)
        token_sha256 = hashlib.sha256(token.encode("utf-8")).hexdigest()
        expires_at = now + timedelta(seconds=settings.authorization_ttl_seconds)
        try:
            authorization_id = database.create_authorization(
                event_id=event.event_id,
                owner_id=request.owner_id,
                token_sha256=token_sha256,
                expires_at=expires_at,
            )
        except EventSupersededError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return AuthorizationResponse(
            authorization_id=authorization_id,
            event_id=event.event_id,
            authorization_token=token,
            expires_at=expires_at,
            warning="令牌仅显示一次、仅限当前事件，并且只能使用一次。",
        )

    @app.post(
        f"{API_PREFIX}/commands/migrate",
        response_model=CommandResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["command"],
    )
    def migrate_command(
        request: MigrationCommandRequest,
        _: str = Security(require_api_key),
    ) -> CommandResponse:
        if settings.actuator_mode == "disabled":
            raise HTTPException(status_code=503, detail="Vehicle actuation is disabled")

        event = database.get_event(request.event_id)
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")
        now = datetime.now(timezone.utc)
        if _event_is_stale(event, settings.event_max_age_seconds, now=now):
            raise HTTPException(status_code=409, detail="Event is stale; ingest fresh telemetry")

        reevaluated = evaluate_decision(
            event.telemetry,
            settings.policy,
            owner_authorized=True,
        )
        if reevaluated.decision != DecisionCode.MIGRATE_NOW or not reevaluated.authorized_to_move:
            raise HTTPException(status_code=409, detail="Fresh safety evaluation denied migration")

        # Only the side-effect-free record-only adapter can reach this branch.
        actuator_result = actuator.migrate_to_high_point(
            event_id=event.event_id,
            vehicle_id=event.telemetry.vehicle_id,
        )
        token_sha256 = hashlib.sha256(request.authorization_token.encode("utf-8")).hexdigest()
        try:
            command_id = database.record_authorized_command(
                event_id=event.event_id,
                token_sha256=token_sha256,
                now=now,
                status=actuator_result.status,
                response=actuator_result.details,
            )
        except EventSupersededError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if not command_id:
            raise HTTPException(status_code=401, detail="Authorization is invalid, expired, or already used")
        return CommandResponse(
            command_id=command_id,
            event_id=event.event_id,
            status="RECORDED_NOT_SENT",
            actuator_mode="record-only",
            message=actuator_result.message,
        )

    @app.get("/styles.css", include_in_schema=False)
    def styles() -> FileResponse:
        return FileResponse(REPO_ROOT / "styles.css", media_type="text/css")

    app.mount("/src", StaticFiles(directory=REPO_ROOT / "src"), name="src")
    app.mount("/assets", StaticFiles(directory=REPO_ROOT / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(REPO_ROOT / "index.html", media_type="text/html")

    return app


app = create_app()
