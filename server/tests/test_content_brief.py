"""主题流与开源项目 AI 中文解读（ContentBrief）、匿名 README 清洗与公开接口联调测试。"""

from __future__ import annotations

from datetime import timedelta, timezone
import json

import pytest
from sqlalchemy import func, select

from app.clock import utcnow
from app.collectors.git_repo import clean_readme_markdown
from app.models import ContentBrief, ContentVersion, RawContent
from app.services.content_brief import (
    brief_content,
    is_brief_eligible,
    parse_brief_json,
    pending_brief_ids,
)
from app.services.ingest import ingest_items


def test_clean_readme_markdown_strips_badges_and_keeps_content():
    raw = """
    <!-- banner -->
    <div align="center">
      <img src="logo.png" width="80" />
    </div>
    [![Build](https://img.shields.io/badge/build-passing-green)](https://ci.example.com)
    ![Demo](demo.gif)

    # Awesome Tool

    A fast CLI for automated code auditing.

    ## Features
    - Zero config
    - Supports MCP
    """
    cleaned = clean_readme_markdown(raw)
    assert "img.shields.io" not in cleaned
    assert "<div" not in cleaned
    assert "<!--" not in cleaned
    assert "# Awesome Tool" in cleaned
    assert "- Supports MCP" in cleaned


def test_parse_brief_json_handles_fences_and_validation():
    raw = """```json
    {
      "headline": "自动化代码审计工具",
      "tagline": "帮助开发者在本地一键扫描安全漏洞并输出修复建议",
      "summary": "基于静态规则与大模型的开源安全审计命令行工具。",
      "highlights": ["零配置即用", "零配置即用", "支持 MCP 协议"],
      "use_cases": ["安全工程师", "CI 流水线"],
      "tags": ["安全测试", "CLI", "安全测试"]
    }
    ```"""
    parsed = parse_brief_json(raw)
    assert parsed["headline"] == "自动化代码审计工具"
    assert parsed["highlights"] == ["零配置即用", "支持 MCP 协议"]
    assert parsed["tags"] == ["安全测试", "CLI"]

    with pytest.raises(ValueError):
        parse_brief_json('{"headline": "", "tagline": ""}')


async def test_content_brief_pipeline_and_public_views(client, session_factory, admin_headers, monkeypatch):
    # 1) 创建开启 ai_summary 的开源主题流分区
    c_resp = await client.post(
        "/api/admin/communities",
        headers=admin_headers,
        json={
            "key": "git",
            "label": "开源",
            "en": "Open Source",
            "color": "violet",
            "domain": "feed",
            "ai_summary": True,
        },
    )
    assert c_resp.status_code == 201
    assert c_resp.json()["ai_summary"] is True

    # 校验资格判定：开源分区开启 ai_summary，小黑盒默认关闭
    assert is_brief_eligible("git", "github", "repo") is True
    assert is_brief_eligible("xiaoheihe", "xiaoheihe", "post") is False
    assert is_brief_eligible("wuwa", "bilibili", "video") is False

    # 2) 入库一条无 README 的 GitHub Trending 仓库与一条小黑盒多图话题帖
    now_iso = (utcnow() - timedelta(hours=1)).replace(tzinfo=timezone.utc).isoformat()
    async with session_factory() as s:
        await ingest_items(
            s,
            "github_trending",
            [
                {
                    "id": "acme/radar:trending:repo",
                    "platform": "github",
                    "kind": "trending",
                    "repo": "acme/radar",
                    "title": "acme/radar",
                    "body": "Lightweight cross-platform event radar",
                    "url": "https://github.com/acme/radar",
                    "published": now_iso,
                    "author": "acme",
                    "language": "Python",
                    "stars": 3200,
                    "forks": 210,
                    "period_stars": 480,
                    "period": "daily",
                    "rank": 1,
                }
            ],
            community="git",
        )
        await ingest_items(
            s,
            "xiaoheihe_posts",
            [
                {
                    "linkid": "xhh12345",
                    "title": "测试帖子[cube_喜欢]开箱分享",
                    "text": "多图评测正文内容，适合卡片预览展示。",
                    "published_at": now_iso,
                    "author": "测试作者",
                    "imgs": [
                        "https://img.example.com/1.jpg",
                        "https://img.example.com/2.jpg",
                        "https://img.example.com/3.jpg",
                        "https://img.example.com/4.jpg",
                    ],
                    "topics": ["数码硬件", "外设评测"],
                    "metrics": {"like": 88, "reply": 12},
                }
            ],
            community="xiaoheihe",
        )
        await s.commit()

        repo_row = await s.scalar(select(RawContent).where(RawContent.external_id == "acme/radar:trending:repo"))
        assert repo_row is not None
        repo_id = repo_row.id

        # 未配置 AI 时安全跳过
        skipped = await brief_content(s, repo_id)
        assert skipped["status"] == "skipped"
        assert skipped["reason"] == "ai_not_configured"

        # 配置模拟 AI 与匿名 README 抓取
        async def fake_ai_cfg(_session, task=None):
            return {
                "base_url": "https://mock-llm.local/v1",
                "api_key": "test-key",
                "api_keys": [],
                "model": "mock-flash",
                "price_in": 0.0,
                "price_out": 0.0,
                "task_models": {},
                "daily_token_limit": 0,
                "daily_cost_limit": 0.0,
            }

        async def fake_readme(repo: str, **_kwargs) -> str:
            return "# Radar\n\nCollects and summarizes open source signals."

        async def fake_chat(config, system, user, *, json_mode=False, task=None):
            assert "Collects and summarizes open source signals" in user
            payload = {
                "headline": "跨平台开源事件信号雷达",
                "tagline": "轻量自托管的多源追踪与信号归并工具，实时捕捉开源项目动态",
                "summary": "Radar 支持从多个开发者平台采集发版与趋势榜单，并在站内完成去重归档与中文摘要。",
                "highlights": ["纯匿名免配额采集", "支持多周期趋势归档"],
                "use_cases": ["独立开发者", "技术团队情报监控"],
                "tags": ["开源雷达", "效率工具"],
            }
            return json.dumps(payload, ensure_ascii=False), {"prompt_tokens": 120, "completion_tokens": 90}

        monkeypatch.setattr("app.services.content_brief.get_ai_config", fake_ai_cfg)
        monkeypatch.setattr("app.collectors.git_repo.fetch_github_repo_readme", fake_readme)
        monkeypatch.setattr("app.services.content_brief.chat", fake_chat)

        pending = await pending_brief_ids(s, community="git")
        assert repo_id in pending

        result = await brief_content(s, repo_id)
        assert result["status"] == "ok"
        assert result["headline"] == "跨平台开源事件信号雷达"

        # 按需补齐 README 不应制造伪修订（仍只有 v1）
        ver_cnt = await s.scalar(select(func.count(ContentVersion.id)).where(ContentVersion.raw_content_id == repo_id))
        assert ver_cnt == 1

        # 已是最新版本时再次执行自动跳过
        again = await brief_content(s, repo_id)
        assert again["status"] == "skipped"
        assert again["reason"] == "already_current"

    # 3) 验证公开列表、话题筛选、详情与主题流总览均携带 AI 解读与多图/话题数据
    list_resp = await client.get("/api/contents", params={"community": "git", "topic": "开源雷达"})
    assert list_resp.status_code == 200
    items = list_resp.json()["items"]
    assert len(items) == 1
    assert items[0]["brief"]["headline"] == "跨平台开源事件信号雷达"
    assert "开源雷达" in items[0]["topics"]

    detail_resp = await client.get(f"/api/contents/{repo_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["brief"]["tagline"].startswith("轻量自托管")
    assert detail["brief"]["highlights"] == ["纯匿名免配额采集", "支持多周期趋势归档"]
    assert "Collects and summarizes" in (detail["repo_meta"]["readme"] or "")

    # 小黑盒列表与主题流总览：多图截断至 3 张 + 总数 4 + 话题聚合 + 话题筛选
    xhh_resp = await client.get("/api/contents", params={"community": "xiaoheihe", "topic": "数码硬件"})
    assert xhh_resp.status_code == 200
    xhh_items = xhh_resp.json()["items"]
    assert len(xhh_items) == 1
    assert len(xhh_items[0]["media_list"]) == 3
    assert xhh_items[0]["media_count"] == 4
    assert "数码硬件" in xhh_items[0]["topics"]

    xhh_ov = await client.get("/api/feeds/overview", params={"community": "xiaoheihe"})
    assert xhh_ov.status_code == 200
    ov_data = xhh_ov.json()
    assert ov_data["total_count"] == 1
    assert ov_data["lead"]["cover"] == "https://img.example.com/1.jpg"
    assert any(t["name"] == "数码硬件" for t in ov_data["topics"])

    # 4) 管理端补全解读接口可正常响应
    brief_api = await client.post("/api/admin/ai/brief-missing", headers=admin_headers, params={"community": "git"})
    assert brief_api.status_code == 200
    assert brief_api.json()["candidates"] == 0
