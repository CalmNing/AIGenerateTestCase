FROM node:18 as build
WORKDIR /app
COPY package.json pnpm-lock.yaml ./ 
RUN npm install pnpm -g
RUN pnpm config set registry https://registry.npmmirror.com/ \
    && pnpm config set strict-ssl false \
    && pnpm install --timeout 180000 --network-concurrency 8
RUN pnpm install
COPY . .
RUN pnpm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8001
CMD ["nginx", "-g", "daemon off;"]