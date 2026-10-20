FROM node:24.11.1-bookworm-slim
WORKDIR /engine
COPY package.json package-lock.json ./
COPY packages ./packages
COPY apps/api ./apps/api
COPY apps/worker ./apps/worker
COPY apps/web/package.json ./apps/web/package.json
COPY industry ./industry
COPY site ./site
RUN npm ci --omit=dev --workspace=@aihot/api --workspace=@aihot/worker --include-workspace-root=false --no-audit --no-fund && npm cache clean --force
COPY database ./database
COPY scripts ./scripts
COPY adapter ./adapter
COPY assets ./assets
COPY LICENSE NOTICE UPSTREAM.json ./
ENV NODE_ENV=production API_HOST=0.0.0.0 API_PORT=3001 AIHOT_DATA_DIR=/data
CMD ["node", "adapter/main.ts"]
