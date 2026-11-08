FROM node:24-alpine

ENV NEXT_TELEMETRY_DISABLED=1
WORKDIR /workspace

COPY website/package.json website/package-lock.json ./website/
COPY backoffice/package.json backoffice/package-lock.json ./backoffice/
RUN npm --prefix website ci \
    && npm --prefix backoffice ci

COPY . .

EXPOSE 3000 3001
CMD ["sh", "./start.sh"]