FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY public public
COPY index.html tsconfig.json tsconfig.app.json tsconfig.node.json \
    vite.config.ts ./
COPY src src
RUN npm run build

FROM nginx:1.31-alpine AS app
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
