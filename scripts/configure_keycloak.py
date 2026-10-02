"""Idempotent local demo accounts. Passwords are written only to ignored files."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets

import httpx

ROOT = Path(__file__).resolve().parents[1]


def local_env() -> dict:
    path = ROOT / ".env"
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line and not line.lstrip().startswith("#")) if path.exists() else {}


def prepare():
    values = local_env()
    path = ROOT / ".env"
    lines = [line for line in path.read_text().splitlines() if not line.startswith(("KC_BOOTSTRAP_ADMIN_PASSWORD=", "AUTH_MODE="))] if path.exists() else []
    lines.append("KC_BOOTSTRAP_ADMIN_PASSWORD=" + (values.get("KC_BOOTSTRAP_ADMIN_PASSWORD") or secrets.token_urlsafe(32)))
    lines.append("AUTH_MODE=oidc")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write("\n".join(lines) + "\n")
    path.chmod(0o600)
    print("Local bootstrap password and OIDC mode ready in ignored .env; no values displayed.")


def configure():
    values = local_env()
    url = "http://localhost:8080"
    with httpx.Client(base_url=url, trust_env=False, timeout=30) as client:
        response = client.post("/realms/master/protocol/openid-connect/token", data={"grant_type":"password", "client_id":"admin-cli", "username":values.get("KC_BOOTSTRAP_ADMIN_USERNAME", "admin"), "password":values["KC_BOOTSTRAP_ADMIN_PASSWORD"]})
        response.raise_for_status()
        client.headers["Authorization"] = "Bearer " + response.json()["access_token"]
        prefix = "/admin/realms/resolveai"
        profile = client.get(prefix + "/users/profile")
        profile.raise_for_status()
        profile_data = profile.json()
        profile_data["attributes"] = [a for a in profile_data["attributes"] if a["name"] != "customer_id"] + [{"name":"customer_id", "displayName":"Customer mapping", "permissions":{"view":["admin"], "edit":["admin"]}, "multivalued":False}]
        client.put(prefix + "/users/profile", json=profile_data).raise_for_status()
        # Update the managed web client when the persisted realm predates this setup.
        realm = json.loads((ROOT / "infra/compose/keycloak-realm.json").read_text())
        for desired in realm["clients"]:
            current = client.get(prefix + "/clients", params={"clientId":desired["clientId"]})
            current.raise_for_status()
            if current.json():
                client.put(prefix + "/clients/" + current.json()[0]["id"], json=desired).raise_for_status()
        output = ROOT / ".local/demo-accounts.json"
        output.parent.mkdir(mode=0o700, exist_ok=True)
        accounts = json.loads(output.read_text()) if output.exists() else {}
        for username, role, customer_id in (("customer-one", "customer", "cust-01"), ("customer-two", "customer", "cust-02"), ("support-demo", "support", None), ("warehouse-demo", "warehouse", None), ("supervisor-demo", "supervisor", None)):
            password = accounts.get(username, {}).get("password") or secrets.token_urlsafe(24)
            user = {"username":username, "enabled":True, "emailVerified":True, "firstName":"Synthetic", "lastName":role, "email":username+"@example.invalid", "attributes":{"customer_id":[customer_id]} if customer_id else {}, "requiredActions":[]}
            existing = client.get(prefix + "/users", params={"username":username, "exact":"true"})
            existing.raise_for_status()
            if existing.json():
                identifier = existing.json()[0]["id"]
                client.put(prefix + "/users/" + identifier, json=user).raise_for_status()
            else:
                created = client.post(prefix + "/users", json=user)
                created.raise_for_status()
                identifier = created.headers["location"].rsplit("/", 1)[-1]
            client.put(prefix + "/users/" + identifier + "/reset-password", json={"type":"password", "value":password, "temporary":False}).raise_for_status()
            role_data = client.get(prefix + "/roles/" + role)
            role_data.raise_for_status()
            client.post(prefix + "/users/" + identifier + "/role-mappings/realm", json=[role_data.json()]).raise_for_status()
            accounts[username] = {"password":password, "role":role, "subject":identifier, "customer_id":customer_id}
            descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                json.dump(accounts, stream, indent=2)
        print(json.dumps({"status":"ok", "accounts":list(accounts), "credentials_file":str(output)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true")
    args = parser.parse_args()
    if args.prepare:
        prepare()
    else:
        configure()
