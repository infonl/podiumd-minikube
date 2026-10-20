"""Sets up, deploys and inspects the minikube cluster; `podiumd-minikube <command> --help` per command."""

import argparse
import sys

from collections.abc import Callable
from pathlib import Path

from podiumd_minikube import chart
from podiumd_minikube import deploy as deployment
from podiumd_minikube import kube
from podiumd_minikube import lock
from podiumd_minikube import ports
from podiumd_minikube import process
from podiumd_minikube import provision
from podiumd_minikube import restart
from podiumd_minikube import services
from podiumd_minikube import status
from podiumd_minikube import teardown
from podiumd_minikube import tunnel
from podiumd_minikube import versions

PROG = "podiumd-minikube"
EXTENDED = [
    "--full",
    *chart.NO_METRICS_SETS,
    "--set", "podiumd.openzaak.worker.replicaCount=1",
    "--set", "podiumd.openklant.worker.replicaCount=1",
]  # fmt: skip

Command = Callable[[list[str]], int | None]


def _parser(command: Command) -> argparse.ArgumentParser:
    """The argument parser of a command, described by its docstring."""
    return argparse.ArgumentParser(
        prog=f"{PROG} {_name(command)}",
        description=command.__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )


def _name(command: Command) -> str:
    return command.__name__.replace("_", "-")


def _yes(command: Command, argv: list[str]) -> bool:
    """Parses a command whose only option is --yes."""
    parser = _parser(command)
    parser.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")
    return parser.parse_args(argv).yes


def cluster_lock(argv: list[str]) -> int:
    """Shows, takes or releases the lock on the shared cluster (also taken by deploy and the other changing commands).

    status: who holds it and since when. take/release: around manual changes.
    run: holds it while a command runs. break: removes a stale lock.
    """
    parser = _parser(cluster_lock)
    parser.add_argument("--who", default=lock.person(), help="holder name for take/release/run (default: your user)")
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("status", help="show the holder")
    take = actions.add_parser("take", help="take it for a manual change")
    take.add_argument("what", help="what you are changing")
    actions.add_parser("release", help="release a lock you took")
    running = actions.add_parser("run", help="hold it while COMMAND runs")
    running.add_argument("what", help="what the command changes")
    running.add_argument("argv", nargs=argparse.REMAINDER, help="-- COMMAND [ARGS...]")
    breaking = actions.add_parser("break", help="remove a stale lock")
    breaking.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args(argv)

    hold = lock.current()
    if args.action == "status":
        print(f"held by {lock.describe(hold)}" if hold else f"free ({lock.LOCK_FILE})")
    elif args.action == "take":
        lock.take(args.what, who=args.who)
        who = "" if args.who == lock.person() else f" --who {args.who}"
        print(f"taken; release with: {PROG} cluster-lock{who} release")
    elif args.action == "release":
        found = lock.release_held_by(args.who)
        if found is not None and found.who == args.who:
            print("released")
        else:
            print(f"not held by {args.who}: {lock.describe(found) if found else 'free'}")
    elif args.action == "run":
        command = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
        if not command:
            parser.error("run needs a command after --")
        return lock.run(args.what, command, who=args.who)
    elif hold is None:
        print("free")
    elif teardown.confirmed(f"Remove the lock held by {lock.describe(hold)}?", yes=args.yes):
        lock.break_lock()
        print("removed")
    return 0


def deploy(argv: list[str]) -> None:
    """Renders this chart and applies it to the minikube cluster; extra arguments (--set x=y) go to helm."""
    _deploy(deploy, argv)


def deploy_extended(argv: list[str]) -> None:
    """deploy --full without metrics, with openzaak's and openklant's Celery workers.

    opennotificaties needs openzaak's worker to publish notifications. Re-running deploy
    with other flags prunes the difference, so this combination is kept here.
    """
    _deploy(deploy_extended, [*EXTENDED, *argv])


def _deploy(command: Command, argv: list[str]) -> None:
    args, extra = deployment.add_arguments(_parser(command)).parse_known_args(argv)
    deployment.deploy(full=args.full, force_prune=args.force_prune, extra=extra)


def expose_postgres(argv: list[str]) -> None:
    """Port-forwards the shared Postgres (user postgres, password postgres) to localhost for GUI tools."""
    parser = _parser(expose_postgres)
    parser.add_argument("port", nargs="?", type=int, default=services.POSTGRES_PORT, help="local port (default 5432)")
    services.expose_postgres(parser.parse_args(argv).port)


def flush_redis(argv: list[str]) -> None:
    """Flushes the shared Redis: every DB (FLUSHALL), or one with --db. DBs mix caches and Celery queues."""
    parser = _parser(flush_redis)
    parser.add_argument("--db", type=int, help="flush only this DB number")
    services.flush_redis(parser.parse_args(argv).db)


def provision_cluster(argv: list[str]) -> None:
    """Creates the minikube cluster: minikube, NGINX Gateway Fabric, dependencies and every image the chart uses.

    Sizes from MINIKUBE_CPUS (default 6) and MINIKUBE_MEMORY in MB (default: half the memory Docker sees).
    """
    _parser(provision_cluster).parse_args(argv)
    provision.provision()


def reset_namespace(argv: list[str]) -> int:
    """Empties the podiumd-minikube namespace and its cluster-wide leftovers, after confirmation."""
    return teardown.reset_namespace(yes=_yes(reset_namespace, argv))


def set_podiumd_version(argv: list[str]) -> None:
    """Selects the podiumd and monitoring-logging dependency versions and fetches them.

    The selection lives in .podiumd-versions.yaml (gitignored); Chart.yaml keeps placeholders.
    After a podiumd change, re-check the image.tag pins in values.yaml (openzaak, objecten,
    opennotificaties, openformulieren). Published versions: helm search repo dimpact/podiumd -l
    """
    parser = _parser(set_podiumd_version)
    parser.add_argument(
        "--path", type=Path, help="local podiumd chart checkout; monitoring-logging follows its sibling"
    )
    parser.add_argument("version", nargs="?", help="published podiumd version")
    parser.add_argument("monitoring_logging", nargs="?", help="published monitoring-logging version")
    args = parser.parse_args(argv)
    if args.path:
        if args.version:
            parser.error("--path takes no version")
        versions.set_path(args.path)
        return
    if not args.version or not args.monitoring_logging:
        parser.error("give <version> <monitoring-logging-version>, or --path <dir>")
    versions.set_version(args.version, args.monitoring_logging)


def setup_tunnel(argv: list[str]) -> None:
    """Starts minikube tunnel in the background and waits for the edge's external IP; `stop` stops it.

    sudo asks for your password in the foreground (for the route). Do not run with sudo.
    """
    parser = _parser(setup_tunnel)
    parser.add_argument("action", nargs="?", choices=["start", "stop"], default="start", help="default: start")
    if parser.parse_args(argv).action == "stop":
        tunnel.stop_tunnel()
    else:
        tunnel.setup_tunnel()


def show_cluster_status(argv: list[str]) -> None:
    """Shows the cluster's state: node and container memory and CPU, profiles, problems, tunnel, lock and disk.

    Reads only; containers are compared with tests/memory-baseline.json.
    """
    parser = _parser(show_cluster_status)
    parser.add_argument("--top", type=int, default=20, help="containers to list, largest first (default 20)")
    parser.add_argument("--all", action="store_true", help="list every container")
    args = parser.parse_args(argv)
    kube.require_minikube_context()
    status.report(0 if args.all else args.top)


def show_podiumd_version(argv: list[str]) -> None:
    """Shows the selected podiumd and monitoring-logging versions and whether they are fetched."""
    _parser(show_podiumd_version).parse_args(argv)
    versions.show()


def show_port_mappings(argv: list[str]) -> None:
    """Shows the edge's entry point and each live Ingress host's backend Service and port."""
    _parser(show_port_mappings).parse_args(argv)
    ports.show_port_mappings()


def start_cluster(argv: list[str]) -> None:
    """Starts the stopped cluster after a reboot: minikube, the apps (ZAC restarted if its boot failed), the tunnel."""
    _parser(start_cluster).parse_args(argv)
    restart.start()


def teardown_cluster(argv: list[str]) -> int:
    """Deletes the whole minikube cluster and stops minikube tunnel, after confirmation."""
    return teardown.teardown_cluster(yes=_yes(teardown_cluster, argv))


def update_hosts(argv: list[str]) -> None:
    """Writes the *.local -> edge IP line to /etc/hosts, replacing earlier ones (uses sudo)."""
    _parser(update_hosts).parse_args(argv)
    tunnel.update_hosts()


COMMANDS: dict[str, Command] = {
    _name(command): command
    for command in (
        cluster_lock,
        deploy,
        deploy_extended,
        expose_postgres,
        flush_redis,
        provision_cluster,
        reset_namespace,
        set_podiumd_version,
        setup_tunnel,
        show_cluster_status,
        show_podiumd_version,
        show_port_mappings,
        start_cluster,
        teardown_cluster,
        update_hosts,
    )
}


def run(argv: list[str]) -> int | None:
    """Runs the command argv names."""
    listing = "\n".join(f"  {name:22} {(command.__doc__ or '').splitlines()[0]}" for name, command in COMMANDS.items())
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=__doc__,
        epilog=f"commands:\n{listing}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("command", choices=COMMANDS, metavar="command", help="one of the commands below")
    parser.add_argument("args", nargs=argparse.REMAINDER, help="its arguments")
    args = parser.parse_args(argv)
    return COMMANDS[args.command](args.args)


def main() -> None:
    """Entry point of the podiumd-minikube command."""
    process.main(lambda: run(sys.argv[1:]))
