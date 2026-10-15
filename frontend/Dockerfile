# Interface SortirIA (Angular), reprise du Dockerfile de Chess Agent (projet 13).
# Dans docker compose, l'API s'appelle « api » : on utilise proxy.docker.json.

FROM node:20-alpine


# Application

WORKDIR /app


# Dépendances

COPY package.json package-lock.json ./

RUN npm ci


# Frontend Angular

COPY . .


# Application

EXPOSE 4200

CMD ["npm", "run", "start", "--", "--host", "0.0.0.0", "--port", "4200", "--proxy-config", "proxy.docker.json"]
