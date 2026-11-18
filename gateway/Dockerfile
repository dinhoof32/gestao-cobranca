FROM node:22-slim
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund
COPY index.mjs .
ENV SESSAO_DIR=/app/sessao PORTA=3000
CMD ["node", "index.mjs"]
