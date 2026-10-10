#!/bin/bash
# Change Logo + boot.js under a database outage: upload a logo, stop Coder's database (CloudNativePG hibernation),
# and check that boot.js (which every Coder page waits for) still answers at once with the cached logo; then bring the
# database back and reset the logo.   test/logo-outage-check.sh <kubeconfig> <ca> <address> [namespace] [cnpg cluster]
set -euo pipefail
export KUBECONFIG=$1; CA=$2; ADDR=$3; NS=${4:-coder}; CL=${5:-coder-db}
URL=https://coder.cp.test
TOK=$(kubectl -n "${NS}" get secret coder-platform-automation -o jsonpath='{.data.token}' | base64 -d)
c() { curl -sS --cacert "${CA}" --connect-to "coder.cp.test:443:${ADDR}:443" -H "Cookie: coder_session_token=${TOK}" -H 'X-Requested-With: coder-ui' "$@"; }
PNG=$(base64 -w0 < <(printf '\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82'))
fail=0; check() { if eval "$2"; then echo "OK    $1"; else echo "FAIL  $1"; fail=1; fi; }

out=$(c -X POST -H 'Content-Type: application/json' -d "{\"dataUrl\":\"data:image/png;base64,${PNG}\"}" "${URL}/__coder-ui/api/logo")
check "logo uploaded" '[[ "${out}" == *"\"set\": true"* ]]'
check "boot.js points at it at once" '[[ "$(c "${URL}/__coder-ui/boot.js")" == *"/__coder-ui/logo?v="* ]]'

echo "-- stopping Coder's database (hibernation)"
kubectl -n "${NS}" annotate cluster.postgresql.cnpg.io "${CL}" cnpg.io/hibernation=on --overwrite >/dev/null
for i in $(seq 1 60); do [[ -z "$(kubectl -n "${NS}" get pods -l "cnpg.io/cluster=${CL}" --no-headers 2>/dev/null | grep -v Completed)" ]] && break; sleep 3; done
sleep 35   # past the 30-second cache age: the next page load triggers a (failing) background refresh
slowest=0
for i in 1 2 3 4 5; do
 t=$(c -o /dev/null -w '%{time_total}' "${URL}/__coder-ui/boot.js"); slowest=$(printf '%s\n%s\n' "${slowest}" "${t}" | sort -g | tail -n1)
 body=$(c "${URL}/__coder-ui/boot.js"); sleep 2
done
check "boot.js answers fast with the database down (slowest ${slowest}s)" 'awk "BEGIN{exit !(${slowest} < 1.0)}"'
check "boot.js still serves the cached logo" '[[ "${body}" == *"/__coder-ui/logo?v="* ]]'
check "Coder's login page loads (HTTP 200)" '[[ "$(c -o /dev/null -w "%{http_code}" -H "Accept: text/html" "${URL}/login")" == 200 ]]'

echo "-- starting the database again"
kubectl -n "${NS}" annotate cluster.postgresql.cnpg.io "${CL}" cnpg.io/hibernation- >/dev/null
kubectl -n "${NS}" wait --for=condition=Ready "cluster.postgresql.cnpg.io/${CL}" --timeout=600s >/dev/null
for i in $(seq 1 30); do [[ "$(c -X POST -H 'Content-Type: application/json' -d '{}' "${URL}/__coder-ui/api/logo/reset")" == *'"ok": true'* ]] && break; sleep 5; done
check "logo reset; boot.js no longer points at it" '[[ "$(c "${URL}/__coder-ui/boot.js")" == *"__cuiLogo=\"\""* ]]'
exit ${fail}
