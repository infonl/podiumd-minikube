# Running this chart on Apple Silicon

This project's tooling (`scripts/provision-cluster.sh`, `scripts/deploy.sh`)
assumes a `minikube` cluster on the **docker driver**, backed by a real
Docker daemon reachable via the `docker` CLI — which is what you get for
free with Docker Desktop. This machine has no Docker Desktop installed, so
that daemon comes from [colima](https://github.com/abiosoft/colima)
instead. Getting there took several wrong turns, logged here so the next
person (or the next `provision-cluster.sh` run) doesn't repeat them.

None of this is specific to *this* chart — it's entirely about getting a
working `minikube --driver=docker` on an Apple Silicon Mac with no Docker
Desktop. Once colima is set up as below, this project's own scripts work
unmodified.

## The one-time colima setup

```bash
brew install colima docker
softwareupdate --install-rosetta --agree-to-license   # one-time, see below
colima start --runtime docker --arch aarch64 --vm-type vz --vz-rosetta \
  --cpus 8 --memory 20 --disk 100
```

Then `scripts/provision-cluster.sh` as documented in the main README - no
further changes needed.

### Why `--arch aarch64`, not `--arch x86_64`

The instinct is to run colima's VM as x86_64 to match this project's
amd64-only images (see `CLAUDE.md`) - **don't**. Confirmed live: an x86_64
colima VM on Apple Silicon runs under full QEMU CPU emulation (there's no
hardware virtualization for a foreign CPU architecture, only for foreign
*instruction sets within* a native-arch VM), which is dramatically slower
and, worse, unreliable - `minikube start`'s own internal SSH/timeout
budgets (6 minutes for host creation) aren't generous enough for it,
causing spurious `DRV_CREATE_TIMEOUT` and `connection refused` failures
that have nothing to do with this chart.

Running the VM as native `aarch64` and using `--vz-rosetta` instead solves
the *actual* problem (amd64 image support) directly: Rosetta 2 translates
individual amd64 *binaries* at near-native speed, registered as a
`binfmt_misc` handler visible to every container sharing that VM's kernel -
including the minikube node's own inner Docker daemon. The node itself
(kubelet, kubeadm, the k8s control plane) runs fully native arm64; only the
actual workload *images* need to be amd64, and those run through Rosetta
transparently. This is exactly the same mechanism Docker Desktop's own
"Use Rosetta for x86/amd64 emulation" setting provides - colima just
requires it to be requested explicitly.

### Why Rosetta needs installing first

`colima start --vz-rosetta` on a machine that's never run Rosetta before
prints a warning and silently falls back to a slower QEMU-based binfmt
handler instead of failing loudly:

```
Unable to enable Rosetta: Rosetta2 is not installed
```

`softwareupdate --install-rosetta --agree-to-license` (no `sudo` needed)
installs it in a few seconds. Re-running `colima start` afterward picks it
up automatically - confirmed live via `colima ssh -- ... binfmt-support`
listing `rosetta` as an available emulator, and a `docker run --platform
linux/amd64 alpine uname -a` completing in ~3.6 seconds including a fresh
image pull.

### Switching an already-running colima instance

colima's `runtime` and `arch` are baked into the instance at creation time
and can't be changed on an existing one - `colima start --runtime docker`
against an instance already running `containerd` just prints `'runtime'
cannot be updated after initial setup, discarded` and keeps the old
runtime. To actually change either, delete and recreate:

```bash
colima stop
colima delete -f --data
colima start --runtime docker --arch aarch64 --vm-type vz --vz-rosetta \
  --cpus 8 --memory 20 --disk 100
```

## `provision-cluster.sh`'s own Apple Silicon fix

Two bugs specific to this architecture combination are already fixed in
`scripts/provision-cluster.sh` (see that file's own comments for the full
"confirmed live" detail) - documented here as the *reason*, not a to-do:

1. **Don't set `DOCKER_DEFAULT_PLATFORM=linux/amd64` globally.** Doing so
   forces the minikube *node* container itself to be amd64 - but
   minikube's own kubeadm/kubelet/kubectl binary selection follows the
   minikube CLI's host `GOARCH` (arm64 here), not the node container's
   platform, so it copies arm64 binaries into an amd64 rootfs and
   `kubeadm init` fails outright (`exec format error` / missing ELF
   interpreter). The node runs native arm64 instead (see above for why
   that's actually the *better* outcome, not a workaround); only the
   `docker pull` calls for the chart's own workload images are forced to
   `--platform linux/amd64`.
2. **`minikube image load <image-ref>` can't load an amd64 image onto an
   arm64 node directly** - it fails with `unable to calculate manifest ...
   content digest ... not found`, trying to resolve the image as if the
   node's own architecture has a matching variant. Saving to a plain
   tarball first (`docker save --platform linux/amd64 ... -o x.tar` then
   `minikube image load x.tar`) sidesteps that arch-aware manifest lookup
   entirely.

## `minikube start --driver=docker`, explicitly

Left on "auto" (minikube's default), a docker-driver failure - including
the exact platform mismatch above, before the fix - makes minikube
silently fall back to its `qemu2` driver instead of failing loudly. That
driver is a same-architecture VM that doesn't solve the amd64-image
problem at all, and isn't what the rest of this project's tooling (e.g.
`provision-cluster.sh`'s own `docker inspect` memory check) assumes.
`provision-cluster.sh` passes `--driver=docker` explicitly so a real
failure surfaces as a real failure instead of a cluster that looks fine
but can't run this chart's images.

## `setup-tunnel.sh` needs a real terminal

`minikube tunnel` (which `scripts/setup-tunnel.sh` wraps) does its own
`sudo` escalation internally, for the one operation that needs it (adding
a network route) - and that needs an actual interactive terminal
(Terminal.app, iTerm, etc.) to prompt for a password. Running it through
an agent/CI-style shell with no real TTY (including Claude Code's own `!`
prefix, which still runs inside a non-interactive session) just fails with
`sudo: a terminal is required to read the password`. Run
`./scripts/setup-tunnel.sh` yourself in a real terminal window instead.

## Test suite

No Apple-Silicon-specific issues here - the standard venv + `playwright
install chromium` setup in the main README's "Testing" section works
as-is once the cluster above is up.
