# Coder for air-gapped clusters: the upstream image plus a Terraform provider mirror, so workspace builds never reach
# registry.terraform.io. The mirror holds exactly the providers (and versions) the bundled workspace template pins:
# stage 1 runs `terraform providers mirror` against that template.
#
#   docker build --build-arg CODER_VERSION=2.37.3 -t coder-platform/coder:v2.37.3 -f images/coder/Dockerfile .
# (build context: the coder-platform directory; ../../build-bundle does this)
ARG CODER_VERSION=2.37.3
ARG TERRAFORM_VERSION=1.15.5

FROM hashicorp/terraform:${TERRAFORM_VERSION} AS providers
COPY chart/coder-platform/files/workspace/*.tf /template/
# The template's @@PLACEHOLDERS@@ are chart values; they don't affect provider resolution.
RUN terraform -chdir=/template providers mirror -platform=linux_amd64 -platform=linux_arm64 /mirror
# Providers other templates on the target need (build-bundle EXTRA_PROVIDERS="hashicorp/aws:5.100.0 ...").
COPY images/coder/extra-providers/ /extra/
RUN if ls /extra/*.tf >/dev/null 2>&1; then terraform -chdir=/extra providers mirror -platform=linux_amd64 -platform=linux_arm64 /mirror; fi

FROM ghcr.io/coder/coder:v${CODER_VERSION}
USER root
COPY --from=providers --chown=1000:1000 /mirror /opt/terraform/plugins
COPY images/coder/terraformrc /opt/terraform/terraformrc
# Coder's provisioner runs terraform with this environment: providers come only from the mirror above.
ENV TF_CLI_CONFIG_FILE=/opt/terraform/terraformrc
USER 1000:1000
