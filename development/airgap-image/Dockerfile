# The fork's Coder for air-gapped clusters: ghcr.io/user187x/coder-platform plus a Terraform provider mirror, so
# template pushes and workspace builds never reach registry.terraform.io (without it `terraform init` fails offline).
# The mirror holds the providers the bundled workspace template needs (template/*.tf, copied in by
# ../tools/build-airgap-image.sh) plus any in extra-providers/*.tf (for your other templates).
ARG BASE=ghcr.io/user187x/coder-platform:2.0.0-platform.4
ARG TERRAFORM_VERSION=1.16.2

FROM hashicorp/terraform:${TERRAFORM_VERSION} AS providers
COPY template/ /template/
RUN terraform -chdir=/template providers mirror -platform=linux_amd64 /mirror
COPY extra-providers/ /extra/
RUN if ls /extra/*.tf >/dev/null 2>&1; then terraform -chdir=/extra providers mirror -platform=linux_amd64 /mirror; fi

FROM ${BASE}
USER root
COPY --from=providers --chown=1000:1000 /mirror /opt/terraform/plugins
COPY terraformrc /opt/terraform/terraformrc
# Coder's provisioner runs terraform with this environment: providers come only from the mirror above.
ENV TF_CLI_CONFIG_FILE=/opt/terraform/terraformrc
USER 1000:1000
