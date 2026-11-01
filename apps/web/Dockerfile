# Web tier (M05): build the React app, serve it from nginx, which also proxies /api to the API.
FROM node:22-alpine AS build
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci
COPY apps/web/index.html apps/web/tsconfig.json apps/web/vite.config.ts ./
COPY apps/web/src ./src
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.28-alpine
COPY apps/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --chmod=755 apps/web/proxy-entrypoint.sh /docker-entrypoint.d/15-filecanopy-proxy.sh
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 8080
