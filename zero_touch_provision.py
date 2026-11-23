#!/usr/bin/env python3
"""TP7 - OPNsense zero-touch provisioning (Kea DHCPv4 + port forward d_nat).
Ré-exécutable : on cherche l'existant avant de créer (add) ou mettre à jour (set).
Usage : python zero_touch_provision.py [config.yaml]
"""
import os
import sys

import requests
import urllib3
import yaml
from dotenv import load_dotenv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
load_dotenv()

BASE = f"https://{os.getenv('OPNSENSE_HOST')}/api"
AUTH = (os.getenv("OPNSENSE_KEY"), os.getenv("OPNSENSE_SECRET"))


def api(method, path, payload=None):
    kwargs = {"json": payload or {}} if method == "POST" else {}
    r = requests.request(
        method, f"{BASE}/{path}", auth=AUTH, verify=False, timeout=15, **kwargs
    )
    r.raise_for_status()
    return r.json()


def check_saved(resp, what):
    if resp.get("result") != "saved":
        raise RuntimeError(f"{what} a échoué : {resp}")


def provision_dhcp(cfg):
    d = cfg["dhcp"]

    resp = api(
        "POST",
        "kea/dhcpv4/set",
        {"dhcpv4": {"general": {"enabled": "1", "interfaces": d["interface"]}}},
    )
    check_saved(resp, "Kea (general)")
    print(f"[DHCP] Kea activé sur '{d['interface']}'")

    rows = api("GET", "kea/dhcpv4/search_subnet")["rows"]
    existing = next((r for r in rows if r.get("subnet") == d["subnet"]), None)

    subnet = {
        "subnet": d["subnet"],
        "pools": d["pool"],
        "description": d.get("description", "TP7 zero-touch"),
        "option_data_autocollect": "0",
        "option_data": {
            "routers": d["router"],
            "domain_name_servers": ",".join(d["dns"]),
        },
    }

    if existing:
        resp = api("POST", f"kea/dhcpv4/set_subnet/{existing['uuid']}", {"subnet4": subnet})
        check_saved(resp, "Kea (set_subnet)")
        print(f"[DHCP] Sous-réseau {d['subnet']} mis à jour")
    else:
        resp = api("POST", "kea/dhcpv4/add_subnet", {"subnet4": subnet})
        check_saved(resp, "Kea (add_subnet)")
        print(f"[DHCP] Sous-réseau {d['subnet']} créé")

    resp = api("POST", "kea/service/reconfigure")
    print(f"[DHCP] reconfigure -> {resp}")


def provision_nat(cfg):
    for r in cfg.get("nat", []):
        rule = {
            "disabled": "0",
            "interface": r["interface"],
            "ipprotocol": "inet",
            "protocol": r["protocol"],
            "destination": {
                "network": r.get("destination", "wanip"),
                "port": str(r["external_port"]),
            },
            "target": r["target_ip"],
            "local-port": str(r["target_port"]),
            "descr": r["name"],
            "pass": "pass",
            "log": "0",
        }

        rows = api("GET", "firewall/d_nat/search_rule")["rows"]
        existing = next(
            (x for x in rows if x.get("descr") == r["name"] and not x.get("is_automatic")),
            None,
        )

        if existing:
            resp = api("POST", f"firewall/d_nat/set_rule/{existing['uuid']}", {"rule": rule})
            check_saved(resp, f"NAT (set_rule {r['name']})")
            print(f"[NAT] Règle '{r['name']}' mise à jour")
        else:
            resp = api("POST", "firewall/d_nat/add_rule", {"rule": rule})
            check_saved(resp, f"NAT (add_rule {r['name']})")
            print(f"[NAT] Règle '{r['name']}' créée")

    resp = api("POST", "firewall/filter/apply")
    print(f"[NAT] apply -> {resp}")


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    if not all([os.getenv("OPNSENSE_HOST"), *AUTH]):
        sys.exit("Variables OPNSENSE_HOST / OPNSENSE_KEY / OPNSENSE_SECRET manquantes dans .env")

    try:
        provision_dhcp(cfg)
        provision_nat(cfg)
        status = api("GET", "kea/service/status")
        print(f"[OK] Kea status : {status}")
    except requests.HTTPError as e:
        sys.exit(f"Erreur HTTP {e.response.status_code} sur {e.request.url}\n{e.response.text[:500]}")
    except (requests.RequestException, RuntimeError) as e:
        sys.exit(f"Erreur : {e}")


if __name__ == "__main__":
    main()