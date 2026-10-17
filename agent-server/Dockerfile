# Luna's agent server: Mastra (on the Vercel AI SDK) with Gemini on Vertex AI. Built with `mastra build` into a
# self-contained Node server (.mastra/output).
FROM node:24-slim AS build
WORKDIR /srv
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY tsconfig.json ./
COPY src ./src
RUN npx mastra build

FROM node:24-slim
WORKDIR /srv
COPY --from=build /srv/.mastra/output ./
ENV NODE_ENV=production MASTRA_HOST=0.0.0.0
USER node
CMD ["node", "index.mjs"]
