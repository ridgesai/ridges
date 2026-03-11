FROM nginx:alpine

USER root

RUN apk add --no-cache gettext
RUN mkdir -p /sandbox-proxy && chmod 0777 /sandbox-proxy

COPY nginx.conf.template /tmp/nginx.conf.template

USER nginx

CMD ["/bin/sh", "-c", "envsubst '${GATEWAY_URL} ${GATEWAY_HOST}' < /tmp/nginx.conf.template > /sandbox-proxy/nginx.conf && exec nginx -c /sandbox-proxy/nginx.conf -g 'daemon off;'"]