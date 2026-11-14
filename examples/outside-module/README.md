# An example outside module

Copy this folder into a new public GitHub repository to start an outside module for World Assistant. Change the
`id`, `name`, `repository` and `image.name` in `wa-module.toml` to your own, then push a version tag (`v1.0.0`):
the workflow in `.github/workflows/release.yml` builds, signs and releases it the way World Assistant checks for.

To try it, open World Assistant's Settings → Outside modules, paste your repository's address and press Read it.

The full guide, with every field of the manifest and every call the module gate offers, is
[docs/outside-modules.md](../../docs/outside-modules.md).
