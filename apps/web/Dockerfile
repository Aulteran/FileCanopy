# Placeholder web tier (M01): static page + reverse proxy to the API. The React app arrives in M05.
FROM nginxinc/nginx-unprivileged:1.28-alpine
COPY apps/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY apps/web/public /usr/share/nginx/html
EXPOSE 8080
