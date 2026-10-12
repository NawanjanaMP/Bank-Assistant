FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY . .
ARG VITE_AUTH_MODE=oidc
ARG VITE_OIDC_ISSUER=http://localhost:8080/realms/bank
ARG VITE_OIDC_CLIENT_ID=bank-assistant-web
ARG VITE_BANK_NAME="Northwind Bank"
ENV VITE_AUTH_MODE=$VITE_AUTH_MODE VITE_OIDC_ISSUER=$VITE_OIDC_ISSUER VITE_OIDC_CLIENT_ID=$VITE_OIDC_CLIENT_ID VITE_BANK_NAME=$VITE_BANK_NAME
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
