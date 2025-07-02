FROM nginx:alpine

# Install envsubst for dynamic configuration
RUN apk add --no-cache gettext

# Copy Nginx configuration template
COPY default.conf.template /etc/nginx/templates/default.conf.template

# Use envsubst to substitute the RIDGES_API_URL env var into the template and run nginx
CMD ["/bin/sh", "-c", "envsubst '$RIDGES_API_URL' < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf && exec nginx -g 'daemon off;'"]
