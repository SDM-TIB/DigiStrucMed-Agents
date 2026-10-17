# Luna's second agent harness: the AI SDK 7 ToolLoopAgent with Gemini on Vertex AI. Node runs the TypeScript sources
# directly (type stripping), so there is no build step.
FROM node:24-slim
WORKDIR /srv
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund
COPY src ./src
ENV NODE_ENV=production
USER node
CMD ["node", "src/server.ts"]
