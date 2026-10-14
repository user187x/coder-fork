# The Coder fork (separate repository)

The Coder server image is built from the fork, which lives in its own repository and is **not** part of this one.

| | |
|---|---|
| Repository | `git@github.com:user187x/coder.git`, branch `main` (local clone: `~/workspace/coder`) |
| Image | `ghcr.io/user187x/coder-platform:2.0.0-platform.35` (`coder version`: `v2.0.0-platform.35+ac6508b`) |
| Built from | `ac6508b0e2969b08f33d479152129c3a35b856be`, clean tree, 2026-10-14 |
| Air-gap image | `…:2.0.0-platform.35-airgap` = the image above + Terraform provider mirror (`../airgap-image/`, `../tools/build-airgap-image.sh`); it is what the chart runs |
| Previous | `2.0.0-platform.34` (`4885d2c81`), `2.0.0-platform.33` (`599afe8ed`), `2.0.0-platform.32` (`716ec4dec`), `2.0.0-platform.31` (`2e7ae6ff9`), `2.0.0-platform.30` (`025b37162`), `2.0.0-platform.29` (`d799ac97b`), `2.0.0-platform.28` (`4e31952b0`), `2.0.0-platform.27` (`6b26be930`), `2.0.0-platform.26` (`100cc87d8`), `2.0.0-platform.25` (`c174bed4b`), `2.0.0-platform.24` (`fafe5314b`), `2.0.0-platform.23` (`0eb28965f`), `2.0.0-platform.22` (`73fed2459`), `2.0.0-platform.21` (`fb996a288`), `2.0.0-platform.20` (`f3887b544`), `2.0.0-platform.19` (`2260748f8`), `2.0.0-platform.18` (`25b73e9ed`), `2.0.0-platform.17` (`73d2c8a54`), `2.0.0-platform.16` (`6e0f2b39b`), `2.0.0-platform.15` (`0a8293071`), `2.0.0-platform.14` (`84b706a1f`), `2.0.0-platform.13` (`237423e43`), `2.0.0-platform.12` (`2bd982755`), `2.0.0-platform.11` (`92daaa856`), `2.0.0-platform.10` (`f6ebdb50e`), `2.0.0-platform.9` (`019d9745b`), `2.0.0-platform.8` (`a68ffe61e`), `2.0.0-platform.7` (`d7a068929`), `2.0.0-platform.6` (`a65bc39b7`), `2.0.0-platform.5` (`ea80521c2`), `2.0.0-platform.4` (`v2.0.0-platform.4+d01b8f6`, built from `d01b8f6` plus uncommitted changes) |

`main` is pushed up to `4885d2c81` (platform.34); `ac6508b0e` (platform.35) is not pushed yet.

**Build requirements:** Go (1.26), Docker, and Node.js **22–24** (the dashboard's `engines`; Node 26 fails `pnpm build`
and its unit tests) with pnpm (`corepack pnpm`). E.g. `PATH=~/.config/nvm/versions/node/v24.21.0/bin:$PATH`, plus a
`pnpm` shim (`exec corepack pnpm "$@"`) on the PATH.

## New fork version

```bash
cd ~/workspace/coder
./scripts/build_platform_image.sh --image ghcr.io/user187x/coder-platform --tag 2.0.0-platform.35 --arch amd64   # [--push]
cd -; ../tools/build-airgap-image.sh 2.0.0-platform.35
```

Then, in this repository: update the pinned commit above, the image tag in
`helm-chart/coder-airgapped/values.yaml` (`coder.coder.image.tag`), the `[custom]` line (tag and digest) in
`helm-chart/images.txt`, `appVersion` + `version` in `helm-chart/coder-airgapped/Chart.yaml`, and `CHANGELOG.md`.

**Following upstream and rolling back:** [`UPSTREAM.md`](UPSTREAM.md) (taking each upstream release: trial merge,
conflict rules, the verification gate) and [`ROLLBACK.md`](ROLLBACK.md) (back to pure upstream Coder and forward
again, with no change to the database or the workspaces).
