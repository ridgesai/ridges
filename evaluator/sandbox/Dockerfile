FROM python:3.14-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends git curl patch diffutils && \
    rm -rf /var/lib/apt/lists/*



# Python
COPY packages_py.txt /tmp/packages_py.txt

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /tmp/packages_py.txt && \
    rm /tmp/packages_py.txt

RUN pip cache purge && \
    rm -rf /root/.cache/pip



# JavaScript
COPY packages_js.txt /tmp/packages_js.txt

ENV NVM_DIR=/root/.nvm

RUN curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash && \
    . "$NVM_DIR/nvm.sh" && \
    nvm install --lts && \
    xargs -a /tmp/packages_js.txt npm install -g && \
    rm /tmp/packages_js.txt && \
    NODE_VERSION=$(nvm version default) && \
    ln -sf "$NVM_DIR/versions/node/$NODE_VERSION/bin/npm" /usr/local/bin/npm && \
    ln -sf "$NVM_DIR/versions/node/$NODE_VERSION/bin/npx" /usr/local/bin/npx && \
    ln -sf "$NVM_DIR/versions/node/$NODE_VERSION/bin/node" /usr/local/bin/node && \
    ln -sf "$NVM_DIR/versions/node/$NODE_VERSION/lib/node_modules" /usr/local/lib/node_modules

ENV NODE_PATH=/usr/local/lib/node_modules



WORKDIR /sandbox