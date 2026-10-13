# Verification report

2026-10-13 06:02 UTC · **22/26 checks passed**

| Suite | Passed | Failed |
|---|---:|---:|
| Unit tests (fork, platform service) | 22 | 4 |

## Unit tests (fork, platform service)

- ❌ go test ./cli — ------------------------------------------------------------------------ FAIL	github.com/coder/coder/v2/cli	40.982s FAIL 
- ❌ go test ./coderd — FAIL FAIL	github.com/coder/coder/v2/coderd	70.004s FAIL 
- ❌ go test ./enterprise/coderd — FAIL FAIL	github.com/coder/coder/v2/enterprise/coderd	96.237s FAIL 
- ❌ dashboard unit tests (vitest, upstream + fork) — ⎯⎯⎯⎯⎯⎯⎯ Failed Tests 8 ⎯⎯⎯⎯⎯⎯⎯  Test Files 2 failed | 335 passed (337)  Tests 8 failed | 4672 passed | 2 skipped (4682) Tests closed successfully but something prevents the main process from exiting 
- ✅ platform service unit tests — OK
- ✅ go test ./cli/cliui — ok  	github.com/coder/coder/v2/cli/cliui	1.074s 
- ✅ go test ./cli/cliutil — ok  	github.com/coder/coder/v2/cli/cliutil	0.014s 
- ✅ go test ./coderd/agentapi — ok  	github.com/coder/coder/v2/coderd/agentapi	0.516s 
- ✅ go test ./coderd/apidoc
- ✅ go test ./coderd/coderdtest — ok  	github.com/coder/coder/v2/coderd/coderdtest	0.615s 
- ✅ go test ./coderd/database — ok  	github.com/coder/coder/v2/coderd/database	3.300s 
- ✅ go test ./coderd/database/dbauthz — ok  	github.com/coder/coder/v2/coderd/database/dbauthz	5.067s 
- ✅ go test ./coderd/database/dbfake
- ✅ go test ./coderd/database/dbmetrics — ok  	github.com/coder/coder/v2/coderd/database/dbmetrics	0.083s 
- ✅ go test ./coderd/database/dbmock
- ✅ go test ./coderd/gitsshkey — ok  	github.com/coder/coder/v2/coderd/gitsshkey	0.213s 
- ✅ go test ./coderd/httpmw — ok  	github.com/coder/coder/v2/coderd/httpmw	0.797s 
- ✅ go test ./coderd/notifications — ok  	github.com/coder/coder/v2/coderd/notifications	2.840s 
- ✅ go test ./coderd/provisionerdserver — ok  	github.com/coder/coder/v2/coderd/provisionerdserver	1.461s 
- ✅ go test ./codersdk — ok  	github.com/coder/coder/v2/codersdk	0.037s 
- ✅ go test ./enterprise/cli — ok  	github.com/coder/coder/v2/enterprise/cli	10.838s 
- ✅ go test ./scripts/typegen
- ✅ go test ./site — ok  	github.com/coder/coder/v2/site	0.274s 
- ✅ go test ./tailnet — ok  	github.com/coder/coder/v2/tailnet	15.538s 
- ✅ go test ./tailnet/test/integration — ok  	github.com/coder/coder/v2/tailnet/test/integration	0.011s 
- ✅ go test ./vpn — ok  	github.com/coder/coder/v2/vpn	1.036s 
