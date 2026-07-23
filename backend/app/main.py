from __future__ import annotations

import hashlib
import hmac
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Response, Security, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles

from .actuator import RecordOnlyActuator, build_actuator
from .config import REPO_ROOT, Settings
from .database import (
    Database,
    EventSupersededError,
    FleetSnapshotConflictError,
    MessageIdConflictError,
    StoredEvent,
)
from .decision_engine import evaluate_decision
from .fleet_models import FleetPlan, FleetSnapshot
from .fleet_planner import plan_fleet
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
RECORD_ONLY_STATUS = "RECORDED_NOT_SENT"
RECORD_ONLY_MODE = "record-only"


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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _verified_record_only_actuator(actuator: object) -> bool:
    # Exact type matching prevents a subclass with an unreviewed side effect
    # from being accepted as the record-only safety boundary.
    return type(actuator) is RecordOnlyActuator


def _capture_time_error(
    payload: TelemetryIn,
    settings: Settings,
    *,
    now: datetime,
) -> str | None:
    if "captured_at" not in payload.model_fields_set:
        return None
    age_seconds = (now - payload.captured_at).total_seconds()
    if age_seconds > settings.capture_max_age_seconds:
        return (
            "captured_at is older than the configured maximum age "
            f"({settings.capture_max_age_seconds}s)"
        )
    if age_seconds < -settings.capture_future_tolerance_seconds:
        return (
            "captured_at is ahead of server time beyond the configured tolerance "
            f"({settings.capture_future_tolerance_seconds}s)"
        )
    return None


def _fleet_capture_time_error(
    payload: FleetSnapshot,
    settings: Settings,
    *,
    now: datetime,
) -> str | None:
    captures = [
        ("captured_at", payload.captured_at),
        *[
            (
                f"vehicles[{item.telemetry.vehicle_id}].telemetry.captured_at",
                item.telemetry.captured_at,
            )
            for item in payload.vehicles
        ],
    ]
    for path, captured_at in captures:
        age_seconds = (now - captured_at).total_seconds()
        if age_seconds > settings.capture_max_age_seconds:
            return (
                f"{path} is older than the configured maximum age "
                f"({settings.capture_max_age_seconds}s)"
            )
        if age_seconds < -settings.capture_future_tolerance_seconds:
            return (
                f"{path} is ahead of server time beyond the configured tolerance "
                f"({settings.capture_future_tolerance_seconds}s)"
            )

    site_age_seconds = (now - payload.site.observed_at).total_seconds()
    if site_age_seconds < -settings.capture_future_tolerance_seconds:
        return (
            "site.observed_at is ahead of server time beyond the configured tolerance "
            f"({settings.capture_future_tolerance_seconds}s)"
        )
    return None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.validate()
    database = Database(settings.database_path)
    actuator = build_actuator(settings.actuator_mode)
    if settings.actuator_mode == RECORD_ONLY_MODE and not _verified_record_only_actuator(actuator):
        raise RuntimeError("record-only mode requires the verified RecordOnlyActuator")

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
        if settings.actuator_mode == RECORD_ONLY_MODE and not _verified_record_only_actuator(
            app.state.actuator
        ):
            raise HTTPException(status_code=503, detail="Record-only actuator verification failed")
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
        capture_error = _capture_time_error(payload, settings, now=_utc_now())
        if capture_error and not database.has_message_id(payload.message_id):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=capture_error)
        result = evaluate_decision(payload, settings.policy)
        try:
            event = database.save_telemetry_and_decision(payload, result)
        except MessageIdConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if event.duplicate:
            response.status_code = status.HTTP_200_OK
        return _event_response(event)

    @app.post(
        f"{API_PREFIX}/fleet/shadow-runs",
        response_model=FleetPlan,
        status_code=status.HTTP_201_CREATED,
        tags=["fleet-shadow"],
    )
    def create_fleet_shadow_run(
        payload: FleetSnapshot,
        response: Response,
        _: str = Security(require_api_key),
    ) -> FleetPlan:
        now = _utc_now()
        capture_error = _fleet_capture_time_error(payload, settings, now=now)
        if capture_error and not database.has_fleet_snapshot_id(payload.snapshot_id):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=capture_error,
            )
        plan = plan_fleet(
            payload,
            settings.policy,
            run_id=f"fleet_{uuid4().hex}",
            created_at=now,
            now=now,
            site_max_age_seconds=settings.capture_max_age_seconds,
        )
        try:
            stored = database.save_fleet_run(payload, plan)
        except FleetSnapshotConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if stored.duplicate:
            response.status_code = status.HTTP_200_OK
        return stored.plan

    @app.get(
        f"{API_PREFIX}/fleet/shadow-runs/{{run_id}}",
        response_model=FleetPlan,
        tags=["fleet-shadow"],
    )
    def get_fleet_shadow_run(
        run_id: str,
        _: str = Security(require_api_key),
    ) -> FleetPlan:
        stored = database.get_fleet_run(run_id)
        if not stored:
            raise HTTPException(status_code=404, detail="Fleet shadow run not found")
        return stored.plan

    @app.get(
        f"{API_PREFIX}/fleet/latest",
        response_model=FleetPlan,
        tags=["fleet-shadow"],
    )
    def get_latest_fleet_shadow_run(
        response: Response,
        site_id: str = Query(min_length=1, max_length=80),
        _: str = Security(require_api_key),
    ) -> FleetPlan:
        stored = database.get_latest_fleet_run(site_id)
        if not stored:
            raise HTTPException(
                status_code=404,
                detail="No fleet shadow run found",
                headers=LATEST_RESPONSE_HEADERS,
            )
        age_seconds = (_utc_now() - stored.received_at).total_seconds()
        if age_seconds > settings.event_max_age_seconds:
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="Latest fleet shadow run is stale; submit a fresh snapshot",
                headers=LATEST_RESPONSE_HEADERS,
            )
        response.headers.update(LATEST_RESPONSE_HEADERS)
        return stored.plan

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
        active_actuator = app.state.actuator
        if settings.actuator_mode == "disabled":
            raise HTTPException(status_code=503, detail="Vehicle actuation is disabled")
        if settings.actuator_mode != RECORD_ONLY_MODE or not _verified_record_only_actuator(
            active_actuator
        ):
            raise HTTPException(status_code=503, detail="Verified record-only actuator is required")

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

        token_sha256 = hashlib.sha256(request.authorization_token.encode("utf-8")).hexdigest()
        if not database.authorization_is_valid(
            event_id=event.event_id,
            token_sha256=token_sha256,
            now=now,
        ):
            raise HTTPException(status_code=401, detail="Authorization is invalid, expired, or already used")

        # The exact-type gate proves this call is the side-effect-free adapter.
        # The atomic consume below remains the final authority under races.
        actuator_result = active_actuator.migrate_to_high_point(
            event_id=event.event_id,
            vehicle_id=event.telemetry.vehicle_id,
        )
        if (
            actuator_result.status != RECORD_ONLY_STATUS
            or actuator_result.mode != RECORD_ONLY_MODE
            or actuator_result.details.get("transmitted") is not False
        ):
            raise RuntimeError("Record-only actuator returned an unverifiable result")
        try:
            command_id = database.record_authorized_command(
                event_id=event.event_id,
                token_sha256=token_sha256,
                now=now,
                status=RECORD_ONLY_STATUS,
                response=actuator_result.details,
            )
        except EventSupersededError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if not command_id:
            raise HTTPException(status_code=401, detail="Authorization is invalid, expired, or already used")
        return CommandResponse(
            command_id=command_id,
            event_id=event.event_id,
            status=RECORD_ONLY_STATUS,
            actuator_mode=RECORD_ONLY_MODE,
            message=actuator_result.message,
        )

    @app.get("/styles.css", include_in_schema=False)
    def styles() -> FileResponse:
        return FileResponse(REPO_ROOT / "styles.css", media_type="text/css")

    app.mount("/src", StaticFiles(directory=REPO_ROOT / "src"), name="src")
    app.mount("/assets", StaticFiles(directory=REPO_ROOT / "assets"), name="assets")
    app.mount("/demo", StaticFiles(directory=REPO_ROOT / "demo"), name="demo")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(REPO_ROOT / "index.html", media_type="text/html")

    return app


app = create_app()
