# Outside modules: a guide for developers

From 0.55.0, anyone can write a module for World Assistant in their own GitHub repository, and anyone running
World Assistant can add it from **Settings → Outside modules** by pasting the repository's address. This page is
the model to follow.

An outside module is a container you build. World Assistant runs it beside itself, sealed off: it can reach only the
**module gate**, which gives it what its owner agreed to and nothing else. Everything it is and asks for is declared
in one file, `wa-module.toml`. The owner sees that declaration in plain words before anything runs.

A complete example to copy is in [`examples/outside-module`](../examples/outside-module/).

## What World Assistant promises the people who install your module

Your module has to fit these rules, so they're worth knowing first:

- **It runs in its own container.** It runs as a non-root user (uid 10001) with a read-only file system, has no
  extra privileges, and has limits on memory, CPU and processes. Its only writable places are `/data` (its own
  storage volume, with a size limit) and `/tmp`.
- **It reaches only the gate.** Its network has no other way out. Outside hosts it declares, and devices on the
  home network the owner gives addresses for, are reached through the gate's proxy (`HTTP_PROXY` and `HTTPS_PROXY`
  are set for you). Anything else is refused and written to its history. A declared host name that resolves to a
  home-network address is refused too.
- **It gets personal data only if the owner ticks it.** It gets each kind separately, and the home location only at
  the precision they choose (never more precise than you asked). Your module must work, perhaps with less, when a
  box isn't ticked: the gate answers 403.
- **Every piece of personal data it's given is written to its history**, which the owner can read.
- **Updates install by themselves** if they pass every check and ask for nothing more. An update that asks for more
  waits for the owner, who sees exactly what's new.
- **Remove takes every trace:** container, storage, images, keys, Home Assistant entities, settings, notifications
  and history. The owner then sees a report of every place checked.

## The repository

```
wa-module.toml                  what it is and what it asks for (below)
Dockerfile                      builds the image
.github/workflows/release.yml   on a tag: build, push, sign, attest, SBOM, lock file (copy the example's)
LICENSE                         an OSI-approved open-source licence, the same one wa-module.toml names
```

The repository must be public. Releases are tags like `v1.2.0`, and a release is installed only from its tag,
never from a branch.

## `wa-module.toml`

```toml
id = "tigo"                        # 2 to 24 of a-z 0-9 _, starting with a letter; never a built-in module's id
name = "Tigo optimisers"           # up to 60 characters
version = "1.2.0"                  # the release, without the v
sdk = 3                            # the module SDK it is written for (3 or later)
min_wa = "0.55.0"                  # the oldest World Assistant it works with
licence = "MIT"                    # an SPDX id; must match the repository's licence
repository = "https://github.com/someone/wa-tigo"
role = "source"                    # source | processor | output: what it mainly does with data
reads = ["the Tigo CCA on the home network"]           # in words: what it reads
gives = ["Home Assistant: power for each panel"]       # in words: what comes out of it
controls = []                      # in words: anything it changes in the real world (empty for most)

[image]
name = "ghcr.io/someone/wa-tigo"   # on ghcr.io, under the repository's own owner; the release says which digest

[keys]                             # shown in Settings → Keys, saved by the owner, given to it as WA_KEY_<NAME>
cca_password = { label = "CCA password", required = false }

[network]
hosts = ["api.example.com", "*.example.net"]   # outside hosts, port 443 or 80
home_devices = ["Tigo CCA"]                    # the owner gives each one's address at install

[data]
home_location = "postcode district"   # none | postcode district | exact
places = false                        # the owner's saved places
vehicles = false                      # vehicle names and registrations
accounts = false                      # usernames and account ids the owner gives it
home_assistant_read = ["sensor.inverter_*"]     # entity ids or patterns it may read
home_assistant_write = ["sensor.wa_x_tigo_*"]   # only its own: wa_x_<id>_...
notifications = true

[purposes]                         # needed when it asks for personal data and reaches outside hosts
"api.example.com" = "the home's postcode district, for the local weather"

[limits]                           # each between World Assistant's floor and ceiling
storage_mb = 50                    # /data (up to 2000)
memory_mb = 256                    # 64 to 1024
settings_fields = 4                # up to 20; World Assistant draws them, never your HTML
pages = 1                          # up to 3
requests_per_minute = 60           # to the gate, up to 600

[[settings]]                       # its settings, drawn by World Assistant on its row in Settings
key = "every"
label = "Read every (seconds)"
type = "number"                    # text | number | boolean | choice (with choices = [...])
default = 30
```

World Assistant checks every field and says which one is wrong, in words, if any is.

## The release

On each version tag, the example's workflow:

1. builds the image and pushes it to `ghcr.io/<owner>/<repo>`;
2. **signs** it keylessly with cosign, using the workflow's identity for that tag;
3. attaches a **build provenance** attestation tying the image to the repository and commit;
4. makes an **SBOM** (SPDX, with syft) and attests it to the image;
5. attaches `wa-module.lock.json` (the image's digest, the tag and the commit) to the GitHub release.

Before installing, World Assistant pulls the image by that digest and checks the following:

- the signature must come from `https://github.com/<owner>/<repo>/.github/workflows/...@refs/tags/<tag>`;
- the provenance and the SBOM attestation must be there;
- a Trivy scan must find no high or critical vulnerabilities (the owner can accept them for one version);
- the repository's licence must match the manifest's.

Integrity rests on the SHA-256 digest and the signature, not on a checksum someone could recompute.

## The module gate

Your container gets these environment variables:

| | |
|---|---|
| `WA_GATE` | `http://wa-modgate:8090` |
| `WA_MODULE` | its id |
| `WA_MODULE_TOKEN` | its own token (send it with each call; never log it) |
| `HTTP_PROXY`, `HTTPS_PROXY` | the gate's proxy, with its token, for the hosts and devices it declared |
| `WA_KEY_<NAME>` | each key the owner has saved |

Every call sends `X-WA-Module: <id>` and `Authorization: Bearer <token>`:

| | |
|---|---|
| `GET /api/me` | its id, version, settings and what the owner agreed |
| `GET /api/home` | the home's location at the agreed precision: `{precision, lat, lon}`, plus `district` for a postcode district |
| `GET /api/places` | the owner's places (if agreed) |
| `GET /api/vehicles` | the owner's vehicles (if agreed) |
| `GET /api/ha/states?ids=a,b` | Home Assistant states, only of the entities it declared |
| `POST /api/ha/entity` | one of its own Home Assistant entities: `{component: sensor or binary_sensor, object_id, name, state, unit, device_class, attributes}` |
| `POST /api/notify` | a notification: `{title, body, key}`; each key is sent once (if it declared notifications) |

Answers are JSON. 401 means the token is wrong or it is no longer installed. 403 means not declared or not agreed.
429 means over its requests a minute.

**Pages:** serve them on port 8080 from your container. World Assistant shows them at `/x/<id>` in a sandboxed
frame: scripts and forms work, but the page can't reach World Assistant's own pages, storage or keys.

## Updates

World Assistant checks each installed module's newest release every day. A release that passes every check and
asks for nothing more installs by itself: it starts beside the running version and switches over, or goes back if
it doesn't start. A release is held, and the owner is told it **needs their attention**, when it:

- asks for any more data, hosts, devices, keys, Home Assistant entities, controls or higher limits;
- has a high or critical vulnerability, or a missing or wrong signature or attestation;
- has changed its licence, or needs a newer World Assistant.

Installed images are scanned again every week. The owner is warned when a module falls behind: an update waiting
more than a week, new vulnerabilities in the running version, or a year without a release while its parts have
known flaws.

**Asking for less never holds an update.** Keep what you ask for small.

## Adoption

Every outside module must be open source. If a module many people use stops being maintained (no release or
reply for a long time, or known vulnerabilities left unfixed), the World Assistant project may fork it, under the
module's own licence, to keep it working. A fork keeps the licence and the original author's credit, uses a new repository
and image, and is offered to owners as a new module. They choose to move to it, and it never replaces yours
silently. This is what open-source licences already allow, so it changes nothing about your rights. It's written
here so nobody is surprised. If you would rather hand a module over yourself, say so in its README.

## Checklist

- [ ] `wa-module.toml` passes (read it in Settings → Outside modules before your first tag)
- [ ] Works with nothing personal ticked
- [ ] Asks only for what it uses, with a purpose for each outside host when it asks for personal data
- [ ] Runs as uid 10001, writes only to `/data` and `/tmp`, serves pages on 8080
- [ ] The release workflow signs, attests and attaches the lock file
- [ ] An open-source `LICENSE` matching the manifest
