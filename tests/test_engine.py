"""Reach engine: set up, doors, device keys, off switch, clean-up and secrets. Agents must not edit this file."""
from harness import A, ok, J, cf_state, cf_set, expect_error, finish, tool_port, PORT

# a pretend Ollama on this machine
OLL = tool_port()
A.PRESETS.insert(0, {"port": OLL, "name": "Ollama", "sub": "ollama", "api": True})

if True:
    ok(A.CF_API == f"http://127.0.0.1:{PORT}", "test hook only accepts this Mac's address")
    st = A.status()
    ok(not any(st["steps"].values()) or st["steps"]["tools"], "fresh install: nothing set up except tools")

    # ---- key
    e = expect_error(A.a_save_key, {"token": "nope"}, "did not accept"); ok(e and "WRONG" not in e, "a wrong key is refused plainly")
    e = expect_error(A.a_save_key, {"token": "notokens"}, "Service Tokens"); ok(e and "WRONG" not in e, "a key missing a permission names the missing row: " + (e or "")[:90])
    A.a_save_key({"token": "noacclist"}, J())
    ok(A.load_state()["account_id"] == "acc1", "a key that can't list accounts: account found from its domain")
    cf_set(org=False)
    e = expect_error(A.a_save_key, {"token": "good"}, "Zero Trust"); ok(e and "WRONG" not in e, "no Zero Trust yet: says to do part A")
    cf_set(org=True)
    A.a_save_key({"token": "good"}, J())
    s = A.load_state(); c = cf_state()
    ok(s["team_domain"] == "myteam.cloudflareaccess.com" and any(i["type"] == "onetimepin" for i in c["idps"]), "key saved; email sign-in codes switched on")
    ok(A.get_secret(A.KEY_SERVICE) == "good" and "good" not in A.STATE_FILE.read_text(), "key lives in the keychain, not the settings file")

    # ---- domain + connect
    A.a_choose_zone({"zone_id": "z1"}, J())
    A.a_connect({}, J())
    s = A.load_state(); tid = s["tunnel_id"]
    plist = A.AGENT_PLIST.read_text()
    ok(A.connector_loaded() and A.status()["connector"]["cloud"] == "healthy", "connector created, started and live")
    ok("TUNNELSECRET" not in plist and "TUNNELSECRET" not in A.STATE_FILE.read_text() and A.get_secret(A.TUNNEL_SERVICE).startswith("TUNNELSECRET"),
       "connector key only in the keychain (not in the startup file or settings)")
    cfg = cf_state()["configs"][tid]
    ok(cfg["ingress"] == [{"service": "http_status:404"}], "before any door: everything answers 'not found'")
    st = A.status(); ok(st["setup_done"] and any(p["port"] == OLL for p in st["detected"]), "set up done; the running tool is detected")

    # ---- add a door
    n0 = len(cf_state()["calls"])
    r = A.a_door_add({"name": "Ollama", "sub": "ai", "port": OLL, "emails": "Me@Example.com, friend@example.org"}, J())
    calls = [(m, p) for m, p, b in cf_state()["calls"][n0:]]
    order = [i for i, (m, p) in enumerate(calls) if (m, p) in (("POST", "/accounts/acc1/access/apps"),) or p.endswith("/dns_records") and m == "POST" or p.endswith("/configurations") and m == "PUT"]
    kinds = [calls[i][1].split("/")[-1] for i in order]
    ok(kinds[0] == "apps" and kinds.index("dns_records") > 0 and kinds.index("configurations") > 0, f"sign-in rule created before the address and route ({kinds})")
    c = cf_state(); app = list(c["apps"].values())[0]; pol = list(c["policies"][app["id"]].values())[0]
    ok(app["domain"] == "ai.example.com" and pol["decision"] == "allow" and [i["email"]["email"] for i in pol["include"]] == ["me@example.com", "friend@example.org"],
       "only the listed people may sign in (emails cleaned)")
    rule = c["configs"][tid]["ingress"][0]
    ok(rule["hostname"] == "ai.example.com" and rule["service"] == f"http://localhost:{OLL}" and rule["originRequest"]["httpHostHeader"] == f"localhost:{OLL}",
       "route to the tool, presented as local for an API")
    ok(rule["originRequest"]["access"]["audTag"] == [app["aud"]] and A.load_state()["origin_check"], "this Mac also checks the sign-in (origin check on)")
    dns = [d for d in c["dns"]["z1"] if d["name"] == "ai.example.com"][0]
    ok(dns["proxied"] and dns["content"] == f"{tid}.cfargotunnel.com", "address points at the connection, through Cloudflare")
    d = A.load_state()["doors"][0]
    ok(A.status()["doors"][0]["local"] and r["url"] == "https://ai.example.com", "door open and its tool answering")

    # ---- bad inputs and partial failures leave nothing open
    for a, why in (({"sub": "AI!", "port": 1, "emails": "a@b.co"}, "bad short name"), ({"sub": "x", "port": 99999, "emails": "a@b.co"}, "bad port"),
                   ({"sub": "x", "port": 80, "emails": "not-an-email"}, "bad email"), ({"sub": "ai", "port": 80, "emails": "a@b.co"}, "duplicate address")):
        e = expect_error(A.a_door_add, a); ok(e is not None, f"refused: {why}")
    cf_set(fail_policy=True); napps = len(cf_state()["apps"])
    e = expect_error(A.a_door_add, {"name": "X", "sub": "x", "port": 8188, "emails": "a@b.co"})
    ok(e and len(cf_state()["apps"]) == napps and len(A.load_state()["doors"]) == 1, "people list refused: sign-in rule rolled back, no door")
    cf_set(fail_policy=False)
    e = expect_error(A.a_door_add, {"name": "T", "sub": "taken", "port": 8188, "emails": "a@b.co"}, "already used")
    s = A.load_state(); t = [x for x in s["doors"] if x["sub"] == "taken"][0]
    hosts = [x.get("hostname") for x in cf_state()["configs"][tid]["ingress"]]
    ok(e and "WRONG" not in e and not t["open"] and "taken.example.com" not in hosts, "address already in use: door stays closed, no route")
    A.a_door_remove({"id": t["id"]}, J())

    # ---- close / open / people
    A.a_door_close({"id": d["id"]}, J())
    ok([x.get("hostname") for x in cf_state()["configs"][tid]["ingress"]] == [None], "closed door: no route")
    A.a_door_open({"id": d["id"]}, J())
    ok(cf_state()["configs"][tid]["ingress"][0]["hostname"] == "ai.example.com", "reopened")
    A.a_door_people({"id": d["id"], "emails": "new@example.com"}, J())
    c = cf_state(); pol = c["policies"][d["app_id"]][d["policy_id"]]
    ok([i["email"]["email"] for i in pol["include"]] == ["new@example.com"], "people list replaced")

    # ---- device keys
    r1 = A.a_device_add({"id": d["id"], "name": "Laptop"}, J())
    r2 = A.a_device_add({"id": d["id"], "name": "Agent server"}, J())
    c = cf_state(); pols = c["policies"][d["app_id"]].values()
    ok(r1["client_secret"].startswith("CLIENTSECRET") and sum(p["decision"] == "non_identity" for p in pols) == 2, "two device keys, each with its own rule")
    ok("CLIENTSECRET" not in A.STATE_FILE.read_text(), "device secrets are never saved")
    dev = A.load_state()["doors"][0]["devices"][0]
    A.a_device_remove({"id": d["id"], "device": dev["id"]}, J())
    c = cf_state()
    ok(dev["token_id"] not in c["svc"] and dev["policy_id"] not in c["policies"][d["app_id"]], "removed key: token and rule deleted")
    r3 = A.a_device_add({"id": d["id"], "name": "Phone"}, J())
    ok(r3["client_secret"], "a new key after a removal still gets a unique rule order")

    # ---- origin check unsupported: falls back, still signed in at Cloudflare
    cf_set(reject_access=True)
    A.a_door_close({"id": d["id"]}, J()); A.a_door_open({"id": d["id"]}, J())
    rule = cf_state()["configs"][tid]["ingress"][0]
    ok("access" not in rule["originRequest"] and not A.load_state()["origin_check"] and not A.status()["origin_check"], "if Cloudflare refuses the origin check: door still opens, screen says so")
    cf_set(reject_access=False)
    A.a_door_close({"id": d["id"]}, J()); A.a_door_open({"id": d["id"]}, J())
    ok(A.load_state()["origin_check"], "origin check back when accepted")

    # ---- off switch
    A.a_connector({"on": False}, J())
    ok(not A.connector_loaded() and A.status()["connector"]["running"] is False, "Close everything stops the connector")
    ok(not A.AGENT_PLIST.exists() and A.status()["steps"]["connect"], "and it stays off after a restart (startup entry removed), set up still counts as done")
    A.a_connector({"on": True}, J())
    ok(A.connector_loaded(), "turned back on")

    # ---- remove: route first, then address, keys, sign-in rule
    n0 = len(cf_state()["calls"])
    A.a_door_remove({"id": d["id"]}, J())
    seq = [(m, p.split("/")[-2] if p.split("/")[-1] not in ("configurations",) else "configurations") for m, p, b in cf_state()["calls"][n0:] if m in ("PUT", "DELETE")]
    ok(seq[0] == ("PUT", "configurations") and seq[-1][0] == "DELETE", f"remove closes the route first ({[s[1] for s in seq]})")
    c = cf_state()
    ok(not c["apps"] and not c["svc"] and not [x for x in c["dns"]["z1"] if x["name"] == "ai.example.com"] and not A.load_state()["doors"], "everything for the door is cleaned up")

    # ---- no secret anywhere on disk outside the keychain
    blob = A.STATE_FILE.read_text() + (A.AGENT_PLIST.read_text() if A.AGENT_PLIST.exists() else "")
    ok(not any(x in blob for x in ("good", "TUNNELSECRET", "CLIENTSECRET")), "no secret in any file Reach writes")
finish()
