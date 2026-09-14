"""HTTP route assembly for the dashboard API."""

from fastapi import APIRouter

from api.routers import admin, debug, decisions, entities, evaluation, matching, search, system

api_router = APIRouter()
for route_module in (
    system,
    search,
    entities,
    evaluation,
    admin,
    matching,
    decisions,
    debug,
):
    api_router.include_router(route_module.router)
