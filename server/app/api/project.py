"""公开项目资料，只提供查询。"""

from fastapi import APIRouter

from app.services.project_github import project_overview

router = APIRouter(prefix="/api/project")


@router.get("/github")
async def github_overview():
    return await project_overview()
