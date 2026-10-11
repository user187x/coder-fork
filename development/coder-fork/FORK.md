# The Coder fork (separate repository)

The Coder server image is built from the fork, which lives in its own repository and is **not** part of this one.

| | |
|---|---|
| Repository | `git@github.com:user187x/coder.git`, branch `main` (local clone: `~/workspace/coder`) |
| Image | `ghcr.io/user187x/coder-platform:2.0.0-platform.14` (`coder version`: `v2.0.0-platform.14+84b706a`) |
| Built from | `84b706a1f191fa8610177451ecb2ba34be292520`, clean tree, 2026-10-11 |
| Air-gap image | `…:2.0.0-platform.14-airgap` = the image above + Terraform provider mirror (`../airgap-image/`, `../tools/build-airgap-image.sh`); it is what the chart runs |
| Previous | `2.0.0-platform.13` (`237423e43`), `2.0.0-platform.12` (`2bd982755`), `2.0.0-platform.11` (`92daaa856`), `2.0.0-platform.10` (`f6ebdb50e`), `2.0.0-platform.9` (`019d9745b`), `2.0.0-platform.8` (`a68ffe61e`), `2.0.0-platform.7` (`d7a068929`), `2.0.0-platform.6` (`a65bc39b7`), `2.0.0-platform.5` (`ea80521c2`), `2.0.0-platform.4` (`v2.0.0-platform.4+d01b8f6`, built from `d01b8f6` plus uncommitted changes) |

`main` is 1 commit ahead of GitHub (`84b706a1f`) until pushed.

**Build requirements:** Go (1.26), Docker, and Node.js **22–24** (the dashboard's `engines`; Node 26 fails `pnpm build`
and its unit tests) with pnpm (`corepack pnpm`). E.g. `PATH=~/.config/nvm/versions/node/v24.21.0/bin:$PATH`, plus a
`pnpm` shim (`exec corepack pnpm "$@"`) on the PATH.

## New fork version

```bash
cd ~/workspace/coder
./scripts/build_platform_image.sh --image ghcr.io/user187x/coder-platform --tag 2.0.0-platform.15 --arch amd64   # [--push]
cd -; ../tools/build-airgap-image.sh 2.0.0-platform.15
```

Then, in this repository: update the pinned commit above, the image tag in
`helm-chart/coder-airgapped/values.yaml` (`coder.coder.image.tag`), the `[custom]` line (tag and digest) in
`helm-chart/images.txt`, `appVersion` + `version` in `helm-chart/coder-airgapped/Chart.yaml`, and `CHANGELOG.md`.
