#!/usr/bin/env python3
"""
find_doorbell.py - locate a Qualvision/Vimar doorbell on the local network.

The doorbell is identified by:
  * its HTTP Server header: "Qualvision -HTTPServer"
  * TCP 34567 open (SDKConst.DEVICE_DEFAULT_STEAM_PORT)

Usage:
  python find_doorbell.py                 # scan 192.168.x.0/24 of this host
  python find_doorbell.py --net 10.0.0.0/24
  python find_doorbell.py --hosts 192.168.1.50,192.168.1.6
"""

from __future__ import annotations

import argparse
import concurrent.futures
import ipaddress
import socket
import ssl
import sys
import urllib.error
import urllib.request

STEAM_PORT = 34567


def local_networks() -> list[str]:
    nets = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith("127.") or ip.startswith("169.254."):
                continue
            if ip.startswith(("10.", "192.168.", "172.")):
                nets.append(str(ipaddress.ip_network(ip + "/24", strict=False)))
    except OSError:
        pass
    return sorted(set(nets)) or ["192.168.1.0/24"]


def tcp_open(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def http_server(host: str, timeout: float = 1.5) -> str:
    for scheme in ("http", "https"):
        url = f"{scheme}://{host}/"
        try:
            ctx = ssl._create_unverified_context() if scheme == "https" else None
            with urllib.request.urlopen(url, timeout=timeout, context=ctx) as r:
                return r.headers.get("Server", "")
        except urllib.error.HTTPError as exc:
            return exc.headers.get("Server", "") if exc.headers else ""
        except Exception:
            continue
    return ""


def probe(host: str) -> dict | None:
    if not tcp_open(host, STEAM_PORT):
        return None
    return {"host": host, "server": http_server(host)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--net", help="CIDR to scan (default: local /24s)")
    ap.add_argument("--hosts", help="comma separated host list")
    ap.add_argument("--width", type=int, default=128, help="concurrency")
    args = ap.parse_args()

    if args.hosts:
        hosts = [h.strip() for h in args.hosts.split(",") if h.strip()]
    else:
        nets = [args.net] if args.net else local_networks()
        hosts = []
        for net in nets:
            print(f"[*] scanning {net}", file=sys.stderr)
            hosts += [str(ip) for ip in ipaddress.ip_network(net).hosts()]

    found = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.width) as ex:
        for res in ex.map(probe, hosts):
            if res:
                found.append(res)
                print(f"[+] {res['host']:15} {res['server'] or '(no Server header)'}")

    doorbells = [f for f in found if "qualvision" in f["server"].lower()]
    print()
    if doorbells:
        for d in doorbells:
            print(f"doorbell: http://{d['host']}/tdkcgi")
        return 0
    print("no Qualvision doorbell found", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
