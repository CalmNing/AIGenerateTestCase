# syntax=docker/dockerfile:1
FROM node:18 AS build
WORKDIR /app

# Layer 1: 依赖安装（package.json 不变时命中 layer cache）
# BuildKit cache mount 在 package.json 变化时仍复用已下载的包
COPY package.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm config set registry https://registry.npmmirror.com/ && \
    npm install

# Layer 2: 构建（最频繁变化放最后）
COPY . .
RUN npm run build

FROM nginx:stable-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8001
CMD ["nginx", "-g", "daemon off;"]
