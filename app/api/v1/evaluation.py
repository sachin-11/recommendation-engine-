"""Offline evaluation: a golden set of queries and runs that score ranking settings on it."""

import uuid

from fastapi import APIRouter, Response, status

from app.api.v1.items import AUTH_RESPONSES, PROTECTED, WRITE
from app.schemas.common import ErrorResponse
from app.schemas.evaluation import EvalQueryIn, EvalQueryOut, EvalRunRequest, EvalRunResponse
from app.services.evaluation.service import EvaluationServiceDep
from app.services.recommendation.dependencies import QueryEngineDep

router = APIRouter(
    prefix="/evaluation", tags=["evaluation"], dependencies=PROTECTED, responses=AUTH_RESPONSES
)


@router.get("/queries", summary="List the golden-set queries")
async def list_queries(service: EvaluationServiceDep) -> list[EvalQueryOut]:
    return [EvalQueryOut.model_validate(q) for q in await service.list_queries()]


@router.post(
    "/queries",
    dependencies=[WRITE],
    status_code=status.HTTP_201_CREATED,
    summary="Add a golden query, or replace the relevant items of an existing one",
    responses={
        200: {"model": EvalQueryOut, "description": "The query existed; its items were replaced"},
        400: {"model": ErrorResponse, "description": "The golden set is full"},
    },
)
async def save_query(
    payload: EvalQueryIn, service: EvaluationServiceDep, response: Response
) -> EvalQueryOut:
    query, created = await service.save_query(payload)
    if not created:
        response.status_code = status.HTTP_200_OK
    return EvalQueryOut.model_validate(query)


@router.delete(
    "/queries/{query_id}",
    dependencies=[WRITE],
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a golden query",
    responses={404: {"model": ErrorResponse, "description": "No such query"}},
)
async def delete_query(query_id: uuid.UUID, service: EvaluationServiceDep) -> None:
    await service.delete_query(query_id)


@router.post(
    "/run",
    dependencies=[WRITE],
    summary="Score ranking settings on the golden set (NDCG, recall, MRR)",
    responses={
        400: {"model": ErrorResponse, "description": "No golden queries, or duplicate names"},
        503: {"model": ErrorResponse, "description": "OpenAI or Pinecone unavailable"},
    },
)
async def run(
    payload: EvalRunRequest, service: EvaluationServiceDep, engine: QueryEngineDep
) -> EvalRunResponse:
    return EvalRunResponse.model_validate(await service.run(engine, payload.k, payload.variants))
