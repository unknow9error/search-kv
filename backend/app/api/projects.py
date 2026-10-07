"""Project-first public-data API, separate from legacy apartment contracts."""

import hashlib
import json
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.api.auth import current_user, rate_limit
from app.core.coordination import BusyError
from app.domain.models import User, aware, utcnow
from app.domain.project_models import DataReportRecord, ProjectFavorite
from app.domain.project_schemas import (
    CatalogSources,
    DataReportOut,
    DataReportPage,
    DataReportRequest,
    ProjectApartmentPage,
    ProjectCompareRequest,
    ProjectCompareResponse,
    ProjectConversationCreate,
    ProjectConversationHistory,
    ProjectConversationOut,
    ProjectConversationPage,
    ProjectCriteria,
    ProjectFacets,
    ProjectLayoutOut,
    ProjectMapResponse,
    ProjectOut,
    ProjectPage,
    ProjectSearchRequest,
    ProjectSort,
    ProjectTurnRequest,
    ProjectTurnResponse,
)
from app.domain.schemas import API_ERROR_RESPONSES, StrictModel, normalize_city

router = APIRouter(prefix="/v1", tags=["projects"], responses=API_ERROR_RESPONSES)


async def result(operation):
    try:
        return await operation
    except ValueError as error:
        code = str(error)
        if code == "project_not_found":
            raise HTTPException(404, code) from None
        if code not in {"invalid_cursor", "invalid_project_comparison", "invalid_limit"}:
            code = "invalid_request"
        raise HTTPException(422, code) from None


async def project(request: Request, identifier: str):
    value = await request.app.state.projects.get(identifier)
    if value is None:
        raise HTTPException(404, "project_not_found")
    return value


async def lock_user(session, user_id):
    if not await session.scalar(select(User).where(User.id == user_id).with_for_update()):
        raise HTTPException(401, "session_expired")


@router.post("/projects/search", response_model=ProjectPage)
async def search(body: ProjectSearchRequest, request: Request, user_id: str = Depends(current_user)):
    return await result(request.app.state.projects.search(body))


@router.get("/projects", response_model=ProjectPage)
async def listing(
    request: Request,
    city: str | None = Query(None, min_length=1, max_length=80),
    q: str | None = Query(None, min_length=1, max_length=200),
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, min_length=1, max_length=4096),
    sort: ProjectSort = "observed_desc",
    user_id: str = Depends(current_user),
):
    return await result(
        request.app.state.projects.search(
            ProjectSearchRequest(
                criteria=ProjectCriteria(city=city, q=q), limit=limit, cursor=cursor, sort=sort
            )
        )
    )


@router.post("/projects/map", response_model=ProjectMapResponse)
async def map_results(
    body: ProjectSearchRequest, request: Request, user_id: str = Depends(current_user)
):
    return await result(request.app.state.projects.map(body))


@router.get("/projects/facets", response_model=ProjectFacets)
async def facets(
    request: Request,
    city: str | None = Query(None, min_length=1, max_length=80),
    user_id: str = Depends(current_user),
):
    return await request.app.state.projects.facets(normalize_city(city))


@router.post("/projects/compare", response_model=ProjectCompareResponse)
async def compare(body: ProjectCompareRequest, request: Request, user_id: str = Depends(current_user)):
    return await result(request.app.state.projects.compare([str(x) for x in body.project_ids]))


class ProjectFavoritesResponse(StrictModel):
    items: list[ProjectOut]


@router.get("/projects/favorites", response_model=ProjectFavoritesResponse)
async def favorites(request: Request, user_id: str = Depends(current_user)):
    async with request.app.state.db.sessions() as session:
        ids = (
            await session.scalars(
                select(ProjectFavorite.project_id)
                .where(ProjectFavorite.user_id == user_id)
                .order_by(ProjectFavorite.created_at.desc(), ProjectFavorite.project_id)
                .limit(200)
            )
        ).all()
    return {"items": await request.app.state.projects.get_many(list(ids))}


@router.put("/projects/favorites/{project_id}", status_code=204)
async def favorite_add(project_id: UUID, request: Request, user_id: str = Depends(current_user)):
    identifier = str(project_id)
    await project(request, identifier)
    try:
        async with request.app.state.coordination.lease("project-favorites:" + user_id, 15):
            async with request.app.state.db.sessions.begin() as session:
                await lock_user(session, user_id)
                if await session.get(ProjectFavorite, (user_id, identifier)):
                    return Response(status_code=204)
                count = await session.scalar(
                    select(func.count())
                    .select_from(ProjectFavorite)
                    .where(ProjectFavorite.user_id == user_id)
                )
                if count >= 200:
                    raise HTTPException(409, "favorites_limit")
                session.add(ProjectFavorite(user_id=user_id, project_id=identifier))
    except BusyError:
        raise HTTPException(409, "favorites_busy") from None
    except IntegrityError:
        async with request.app.state.db.sessions() as session:
            if not await session.get(ProjectFavorite, (user_id, identifier)):
                raise
    return Response(status_code=204)


@router.delete("/projects/favorites/{project_id}", status_code=204)
async def favorite_delete(project_id: UUID, request: Request, user_id: str = Depends(current_user)):
    try:
        async with request.app.state.coordination.lease("project-favorites:" + user_id, 15):
            async with request.app.state.db.sessions.begin() as session:
                await session.execute(
                    delete(ProjectFavorite).where(
                        ProjectFavorite.user_id == user_id,
                        ProjectFavorite.project_id == str(project_id),
                    )
                )
    except BusyError:
        raise HTTPException(409, "favorites_busy") from None
    return Response(status_code=204)


@router.post("/projects/conversations", status_code=201, response_model=ProjectConversationOut)
async def conversation_create(
    body: ProjectConversationCreate, request: Request, user_id: str = Depends(current_user)
):
    await rate_limit(request, "project-conversation-create:" + user_id, 20)
    return await request.app.state.project_chat.create(user_id, body)


@router.get("/projects/conversations", response_model=ProjectConversationPage)
async def conversations(request: Request, user_id: str = Depends(current_user)):
    return await request.app.state.project_chat.list(user_id)


@router.get("/projects/conversations/{conversation_id}", response_model=ProjectConversationHistory)
async def history(
    conversation_id: UUID,
    request: Request,
    before: UUID | None = None,
    user_id: str = Depends(current_user),
):
    return await request.app.state.project_chat.history(user_id, conversation_id, before)


@router.delete("/projects/conversations/{conversation_id}", status_code=204)
async def conversation_delete(
    conversation_id: UUID, request: Request, user_id: str = Depends(current_user)
):
    await request.app.state.project_chat.delete(user_id, conversation_id)
    return Response(status_code=204)


@router.post("/projects/conversations/{conversation_id}/turns", response_model=ProjectTurnResponse)
async def turn(
    conversation_id: UUID,
    body: ProjectTurnRequest,
    request: Request,
    user_id: str = Depends(current_user),
):
    await rate_limit(request, "project-turn:" + user_id, 20)
    return await request.app.state.project_chat.turn(user_id, conversation_id, body)


@router.get("/catalog/sources", response_model=CatalogSources)
async def sources(request: Request, user_id: str = Depends(current_user)):
    return await request.app.state.projects.sources()


def report_fingerprint(body):
    return hashlib.sha256(
        json.dumps(body.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def present_report(row):
    return DataReportOut(
        id=row.id,
        client_report_id=row.client_report_id,
        project_id=row.project_id,
        category=row.category,
        message=row.message,
        created_at=aware(row.created_at),
    )


@router.post("/data-reports", status_code=201, response_model=DataReportOut)
async def report_create(body: DataReportRequest, request: Request, user_id: str = Depends(current_user)):
    fingerprint = report_fingerprint(body)
    try:
        async with request.app.state.coordination.lease("data-report:" + user_id, 15):
            async with request.app.state.db.sessions.begin() as session:
                await lock_user(session, user_id)
                previous = await session.scalar(
                    select(DataReportRecord).where(
                        DataReportRecord.user_id == user_id,
                        DataReportRecord.client_report_id == str(body.client_report_id),
                    )
                )
                if previous:
                    original = DataReportRequest(
                        client_report_id=previous.client_report_id,
                        project_id=previous.project_id,
                        category=previous.category,
                        message=previous.message,
                    )
                    if report_fingerprint(original) != fingerprint:
                        raise HTTPException(409, "idempotency_conflict")
                    return present_report(previous)
                await project(request, str(body.project_id))
                await rate_limit(request, "data-report-create:" + user_id, 20, 3600)
                count = await session.scalar(
                    select(func.count())
                    .select_from(DataReportRecord)
                    .where(DataReportRecord.user_id == user_id)
                )
                if count >= 200:
                    raise HTTPException(409, "data_reports_limit")
                row = DataReportRecord(
                    user_id=user_id,
                    project_id=str(body.project_id),
                    client_report_id=str(body.client_report_id),
                    category=body.category,
                    message=body.message,
                )
                session.add(row)
                await session.flush()
                return present_report(row)
    except BusyError:
        raise HTTPException(409, "data_reports_busy") from None


@router.get("/data-reports", response_model=DataReportPage)
async def reports(request: Request, user_id: str = Depends(current_user)):
    async with request.app.state.db.sessions() as session:
        rows = (
            await session.scalars(
                select(DataReportRecord)
                .where(DataReportRecord.user_id == user_id)
                .order_by(DataReportRecord.created_at.desc(), DataReportRecord.id)
                .limit(200)
            )
        ).all()
    return {"items": [present_report(row) for row in rows]}


@router.get("/projects/{project_id}", response_model=ProjectOut)
async def detail(project_id: UUID, request: Request, user_id: str = Depends(current_user)):
    return await project(request, str(project_id))


@router.get("/projects/{project_id}/apartments", response_model=ProjectApartmentPage)
async def apartments(
    project_id: UUID,
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, min_length=1, max_length=4096),
    user_id: str = Depends(current_user),
):
    value = await result(request.app.state.projects.apartments(str(project_id), limit, cursor))
    if value is None:
        raise HTTPException(404, "project_not_found")
    return value


class ProjectLayoutsResponse(StrictModel):
    items: list[ProjectLayoutOut]


@router.get("/projects/{project_id}/layouts", response_model=ProjectLayoutsResponse)
async def layouts(project_id: UUID, request: Request, user_id: str = Depends(current_user)):
    await project(request, str(project_id))
    return {"items": await request.app.state.projects.layouts(str(project_id))}


@router.get("/project-layouts/{layout_id}", response_model=ProjectLayoutOut)
async def layout(layout_id: UUID, request: Request, user_id: str = Depends(current_user)):
    value = await request.app.state.projects.layout(str(layout_id))
    if value is None:
        raise HTTPException(404, "layout_not_found")
    return value


class ProjectRefreshResponse(StrictModel):
    project: ProjectOut
    provider_status: str
    checked_at: datetime
    availability_confirmed: Literal[False] = False


@router.post("/projects/{project_id}/refresh", response_model=ProjectRefreshResponse)
async def refresh(project_id: UUID, request: Request, user_id: str = Depends(current_user)):
    await rate_limit(request, "project-refresh:" + user_id, 6)
    value = await project(request, str(project_id))
    provider = request.app.state.catalog.providers.get(value.provider_id)
    if provider is None:
        raise HTTPException(404, "project_not_found")
    status = await request.app.state.catalog.refresh_provider(provider, city=value.city)
    return {
        "project": await project(request, str(project_id)),
        "provider_status": status["status"],
        "checked_at": utcnow(),
        "availability_confirmed": False,
    }
