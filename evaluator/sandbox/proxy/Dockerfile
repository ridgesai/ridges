# Sandbox Proxy Docker Image
#
# This Dockerfile builds the nginx proxy that bridges isolated sandboxes
# to the inference gateway. It is the ONLY container with internet access.
#
# Architecture:
# - Runs on the internal sandbox network (so sandboxes can reach it)
# - Also connected to the default bridge network (for internet access)
# - Forwards HTTP requests from sandboxes to the inference gateway
#
# Security:
# - Minimal attack surface based on nginx:alpine
# - No unnecessary packages beyond gettext for config templating
# - Configuration is generated at runtime from template
# - No hardcoded credentials or URLs in the image
#
# Runtime Environment Variables:
# - GATEWAY_URL: Full URL of the inference gateway (e.g., http://host:8000)
# - GATEWAY_HOST: Hostname extracted from GATEWAY_URL for proxy headers

FROM nginx:alpine

# gettext provides envsubst for runtime configuration templating
RUN apk add --no-cache gettext

# nginx configuration template - variables substituted at runtime
COPY nginx.conf.template /tmp/nginx.conf.template

# Generate final nginx config from template using environment variables, then start nginx
# daemon off keeps nginx in foreground (required for Docker containers)
CMD ["/bin/sh", "-c", "envsubst '${GATEWAY_URL} ${GATEWAY_HOST}' < /tmp/nginx.conf.template > /etc/nginx/nginx.conf && exec nginx -g 'daemon off;'"]