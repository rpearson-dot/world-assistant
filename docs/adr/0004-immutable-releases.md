# 0004 Immutable, signed releases

**Status:** accepted

**Context.** The server should run exactly what was reviewed and tested, be reproducible, and make
it easy to say what is inside (dependencies, base image) when a vulnerability is announced.

**Decision.**
- Build once in CI, publish to GHCR, deploy by digest. No building on the server for production.
- Hash-locked Python dependencies; two-stage image; non-root, read-only root filesystem, no
  capabilities.
- Base image resolved to a digest at release time and recorded in the provenance.
- SBOMs (SPDX and CycloneDX) attached to the image, as attestations, and to the GitHub release.
- Keyless Sigstore signing and a build provenance attestation; Trivy scan on release and weekly.
- Actions pinned to commit SHAs; Dependabot proposes updates.

**Consequences.** Upgrades and rollbacks are a one-line change of digest. The release workflow
cannot be run from a laptop, by design. The first release will probably need small fixes to the
workflows, since they cannot be tested without GitHub.
