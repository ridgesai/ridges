FROM nginx:alpine

RUN apk add --no-cache gettext

COPY nginx.conf.template /tmp/nginx.conf.template

CMD ["/bin/sh", "-c", "envsubst '${GATEWAY_URL} ${GATEWAY_HOST}' < /tmp/nginx.conf.template > /etc/nginx/nginx.conf && exec nginx -g 'daemon off;'"]