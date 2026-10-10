# The Coder fork (separate repository)

The Coder server image is built from the fork, which lives in its own repository and is **not** part of this one.

| | |
|---|---|
| Repository | `git@github.com:user187x/coder.git`, branch `main` (local clone: `~/workspace/coder`) |
| Image | `ghcr.io/user187x/coder-platform:2.0.0-platform.4` (`coder version`: `v2.0.0-platform.4+d01b8f6`) |
| Built from | `d01b8f6148630b11eea7c87a9973d98117dd8a26` (`1.0.0-5`) **plus uncommitted changes**, 2026-10-08 04:23 |
| Pinned commit | `9c74cd2b1ae26a15503b2459d56ec2d0bde26b7f` (`1.0.0-6`, "User menu: no Avatar item"), committed one minute after the build – most likely exactly those changes |
| Air-gap image | `…:2.0.0-platform.4-airgap` = the image above + Terraform provider mirror (`../airgap-image/`, `../tools/build-airgap-image.sh`); it is what the chart runs |

As of 2026-10-10 the pinned commit is **not pushed** to GitHub (`main` is 1 ahead of `origin/main`). A backup of the
full history is in `~/coder-airgapped-private/coder-fork.bundle`.

## New fork version

```bash
cd ~/workspace/coder
./scripts/build_platform_image.sh --image ghcr.io/user187x/coder-platform --tag 2.0.0-platform.5   # [--push]
cd -; ../tools/build-airgap-image.sh 2.0.0-platform.5
```

Then, in this repository: update the pinned commit above, the image tag in
`helm-chart/coder-airgapped/values.yaml` (`coder.coder.image.tag`), the `[custom]` line (tag and digest) in
`helm-chart/images.txt`, `appVersion` + `version` in `helm-chart/coder-airgapped/Chart.yaml`, and `CHANGELOG.md`.
