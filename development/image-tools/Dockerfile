# The platform service's image (coder-ui-updates) and the image builder's scan step: the bundled Python plus the tools
# the Images builder needs, all offline:
#   - shellcheck (lints installation scripts) and shfmt (formats them, `shfmt -i 1`);
#   - trivy with its vulnerability database preloaded (/opt/trivy-cache), so images are scanned without network;
#   - regctl, to read built images from the registry (size, layers, download as a tarball).
# Build with ../tools/build-image-tools.sh, which downloads the pinned binaries (checked against their checksums) and
# the trivy database, and saves the image into the delivery bundle.
ARG BASE=docker.io/library/python:3.13-alpine@sha256:2d9aefe2fef018a7eb2c13064c89c71929800fd2e5dccdbf52ea5da5bb8d929a
FROM ${BASE}
COPY bin/ /usr/local/bin/
COPY trivy-cache/ /opt/trivy-cache/
RUN chmod -R a+rX /opt/trivy-cache /usr/local/bin \
	&& shellcheck --version >/dev/null && shfmt --version >/dev/null && trivy --version >/dev/null \
	&& regctl version >/dev/null
# trivy never updates its database here: the bundle's database is used (a newer one can be uploaded in the Images view).
ENV TRIVY_CACHE_DIR=/opt/trivy-cache TRIVY_SKIP_DB_UPDATE=true TRIVY_SKIP_JAVA_DB_UPDATE=true TRIVY_OFFLINE_SCAN=true \
	TRIVY_NO_PROGRESS=true
