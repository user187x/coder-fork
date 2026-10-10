# The "existing" Coder of the upgrade rehearsal: 2.36.0 air-gap image whose provider mirror has what the legacy
# template needs (kubernetes 2.x, random) - different from the bundle's mirror on purpose.
FROM hashicorp/terraform:1.15.5 AS providers
COPY template/main.tf /t/
RUN terraform -chdir=/t providers mirror -platform=linux_amd64 /mirror
FROM ghcr.io/coder/coder:v2.36.0
USER root
COPY --from=providers --chown=1000:1000 /mirror /opt/terraform/plugins
RUN printf 'provider_installation {\n  filesystem_mirror {\n    path = "/opt/terraform/plugins"\n    include = ["*/*"]\n  }\n  direct {\n    exclude = ["*/*"]\n  }\n}\n' > /opt/terraform/terraformrc
ENV TF_CLI_CONFIG_FILE=/opt/terraform/terraformrc
USER 1000:1000
