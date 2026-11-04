# Web tier (M05): build the React app, serve it from nginx, which also proxies /api to the API.
FROM node:22-alpine@sha256:fe4f6d74e20370acb04ad889077d14d0f64a9c8ea6a42bc60cddf1e844699914 AS build
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci
COPY apps/web/index.html apps/web/tsconfig.json apps/web/vite.config.ts ./
COPY apps/web/THIRD_PARTY_NOTICES.md ./
COPY LICENSE /LICENSE
COPY apps/web/public ./public
COPY apps/web/src ./src
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.28-alpine@sha256:7377697a821c131a924a7105fafbe7414db4e9fcc77a6f08f776f33f141ec3f8
COPY apps/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --chmod=755 apps/web/proxy-entrypoint.sh /docker-entrypoint.d/15-filecanopy-proxy.sh
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 8080
