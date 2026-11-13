# Contributing to AniSignal

感谢参与改进 AniSignal！提交改动前请先阅读以下简明约定。

## 本地开发环境

需要 **Python 3.12+、uv、Node.js 22 与 npm**（本地开发无需安装 MySQL、Redis 或 Docker）：

```bash
# 后端（SQLite + 进程内队列，默认管理令牌 local-dev）
cd server
uv run python -m app.local

# 前端（http://localhost:3000）
cd web
npm ci
npm run dev
```

## 提交前检查

1. **运行测试与类型检查**：
   ```bash
   uv run --directory server pytest -q
   npm --prefix web test
   npm --prefix web run build
   ```
2. **数据库变更**：
   - 修改 `server/app/models/` 时，需同步编写 Alembic 迁移脚本（位于 `server/alembic/versions/`），并确保空 MySQL 8.4 执行 `alembic upgrade head && alembic check` 通过（CI 会自动校验）。
   - 不要在迁移脚本中硬编码个人站点种子数据。
3. **隐私与安全**：
   - 严禁提交任何真实 Cookie、API Key、服务器 IP、私人域名或个人未公开邮箱。
   - 新增采集平台或外部 URL 处理须遵循现有 SSRF 校验（`app/services/urls.py`）与子进程参数收敛规则。
4. **代码风格**：
   - 保持最小完整改动：复用仓库既有分层与组件，不引入无调用方的抽象或多余依赖；非平凡分支逻辑请附带对应测试。

## 许可证

提交至本仓库的代码默认遵循项目根目录 [LICENSE](LICENSE)（GNU AGPL v3.0）。
