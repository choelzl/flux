"""`flux serve` and `flux user` (D683)."""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path


def data_dir(given: str | None) -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(given or Path(base) / "flux" / "web")


def serve(args) -> int:
    import uvicorn

    from . import create_app
    from .store import Store

    data = data_dir(args.data)
    if not Store(data).users():
        print(f"flux serve: no account yet; make one with `flux user add NAME --admin --data {data}`", file=sys.stderr)
        return 2
    if args.no_sandbox:
        print("flux serve: runs go on this machine unsandboxed (--no-sandbox): for one trusted user only", file=sys.stderr)
    if args.host not in ("127.0.0.1", "localhost", "::1") and not args.secure_cookie:
        print("flux serve: listening beyond this machine without --secure-cookie: put a TLS proxy in front "
              "and pass --secure-cookie", file=sys.stderr)
    app = create_app(data, sandbox=not args.no_sandbox, secure_cookie=args.secure_cookie, max_running=args.max_running)
    print(f"flux serve: http://{args.host}:{args.port}/ (data {data})", file=sys.stderr, flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def user(args) -> int:
    from .store import Store

    store = Store(data_dir(args.data))
    if args.action == "list":
        for u in store.users():
            print(f"{u.name:20} {u.role:6} {'disabled' if u.disabled else ''}")
        return 0
    if not args.name:
        print(f"flux user {args.action}: which user?", file=sys.stderr)
        return 2
    try:
        if args.action == "add":
            pw = getpass.getpass(f"password for {args.name}: ")
            if pw != getpass.getpass("again: "):
                print("the two differ", file=sys.stderr)
                return 2
            store.add_user(args.name, pw, "admin" if args.admin else "user")
        elif args.action == "passwd":
            pw = getpass.getpass(f"new password for {args.name}: ")
            store.set_user(args.name, password=pw)
        else:
            store.set_user(args.name, disabled=args.action == "disable")
    except ValueError as exc:
        print(f"flux user {args.action}: {exc}", file=sys.stderr)
        return 2
    store.audit(None, f"cli {args.action}", args.name)
    print(f"{args.action}: {args.name}")
    return 0
