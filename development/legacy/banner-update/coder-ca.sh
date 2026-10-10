# Sourced by verify-menu / uninstall-banner-chart. Sets CA_OPTS: the curl options that make it trust Coder's certificate.
#   CODER_CA_FILE    path to the CA certificate (PEM) that signed Coder's certificate
#   CODER_CA_SECRET  else: a Secret in the Coder namespace with key ca.crt   [tls-ca]
# If neither exists the system trust store is used (fine when Coder has a publicly trusted certificate).
# Usage:  . ./coder-ca.sh "<scratch dir>"
CA_OPTS=()
if [[ -n "${CODER_CA_FILE:-}" ]]; then
 [[ -r "${CODER_CA_FILE}" ]] && CA_OPTS=(--cacert "${CODER_CA_FILE}") || echo "warning: CODER_CA_FILE ${CODER_CA_FILE} is not readable" >&2
else
 if kubectl get secret "${CODER_CA_SECRET:-tls-ca}" -n "${CODER_NAMESPACE:-coder}" -o jsonpath='{.data.ca\.crt}' 2>/dev/null | base64 -d >"$1/ca.crt" 2>/dev/null && [[ -s "$1/ca.crt" ]]; then
  CA_OPTS=(--cacert "$1/ca.crt")
 fi
fi
