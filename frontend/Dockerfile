FROM node:18 AS build
WORKDIR /app
COPY package.json ./
RUN npm config set registry https://registry.npmmirror.com/ && \
    npm cache clean --force && \
    npm install
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8001
CMD ["nginx", "-g", "daemon off;"]