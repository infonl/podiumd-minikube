# Laptop resources

podiumd-minikube runs on personal laptops, for local tests only. It stays as
similar to production as possible (see [[reference-environments]]), but a
laptop has limited resources: typically 32 GiB of memory at most, shared with
the IDE, a browser and other applications. Memory is the scarcest resource,
then CPU.

**Why:** the user's rule (2026-10-08). An environment that doesn't fit on the
laptop next to normal work isn't used, and a node that swaps fails in ways
production never does (seen live: a 16 GiB cap thrashed until etcd restarted).

**How to apply:**

- Budget: the default profile must run well within about 12–16 GiB for the
  minikube node; `--full` should aim for at most about 20 GiB. Every new
  component states its memory request/limit and its measured settled usage
  in plan.md.
- Scale like production in shape, not in size: one replica everywhere, the
  smallest worker/process counts that keep behaviour identical, heap sizes
  set explicitly for JVM/Elasticsearch.
- Keep production behaviour that tests depend on (auth, TLS, edge buffering,
  limits); shrink only capacity (replicas, heaps, pools, caches, retention).
- Optional profiles stay off by default; heavy ones (Elasticsearch, monitoring,
  Frank!Gateway/OpenBao) are opt-in, and it is cheap to run only the profiles
  a test needs.
- When a change adds memory or CPU, propose ways to offset it before adding
  it; measure before and after (`docker stats minikube --no-stream`).
- Proactively propose savings with measured numbers, ranked by memory saved
  and by risk to production fidelity; never cut what the tests exercise.

Related: [[reference-environments]], [[remove-unused]].
