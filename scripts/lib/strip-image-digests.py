#!/usr/bin/env python3
"""
Helm post-renderer (see `helm template/install --post-renderer`). Strips
any "@sha256:..." digest suffix from every container image reference in the
rendered manifest, regardless of what tag it's attached to.

Why this needs to exist at all: minikube's inner Docker has no outbound
network access, so every image this chart uses has to be pre-pulled on the
host and `minikube image load`ed by tag. kubelet's own exact-reference
matching for a digest-qualified image ("repo:tag@sha256:...") does not
recognize a tag-only loaded image as satisfying it, and always attempts a
live pull instead - which then fails. Stripping the digest and keeping just
the tag fixes this (same underlying image content either way, just pulled
by tag).

Why this is a post-renderer and not a values.yaml override: several
earlier fixes hardcoded specific tag values in values.yaml to work around
this (e.g. `podiumd.zac.image.tag: "5.0.1"`) - but that value was never an
intentional version pin, it was just "whatever the chart's bundled default
already was, with the digest removed". A hardcoded tag silently goes stale
the moment `scripts/set-podiumd-version.sh` selects a different podiumd
release whose bundled subcharts default to different versions - the
override would then downgrade (or otherwise diverge from) whatever the
newly-selected podiumd version actually intends, for every image affected
this way. Operating on the fully-rendered manifest instead means this
always tracks whatever tag the currently-selected podiumd version's bundled
charts actually specify, with zero hardcoded versions to go stale.

A handful of *actual* version pins remain in values.yaml on top of this
(openzaak, objecten, opennotificaties, openformulieren) - those are
deliberately different from any podiumd version's own bundled default, for
real functional reasons (schema compatibility with vendored fixture SQL, or
matching docker-compose.yaml's own pinned version exactly), not a
digest-pull workaround. See values.yaml's own comments on each of those for
why - they are correctly hardcoded and this script leaves the tag portion
of those references untouched, only ever removing an "@sha256:..." suffix
if one is present.
"""
import re
import sys

DIGEST_SUFFIX = re.compile(r"(:[^\s@]+)@sha256:[0-9a-f]{64}\b")

for line in sys.stdin:
    sys.stdout.write(DIGEST_SUFFIX.sub(r"\1", line))
