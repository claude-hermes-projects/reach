"""Card 001 · Move all doors to another domain. Acceptance test: agents must not edit this file."""
from harness import A, ok, J, cf_state, cf_set, expect_error, finish, connect, tool_port

connect("z1")
p1, p2 = tool_port(), tool_port()
A.a_door_add({"name": "Chat", "sub": "chat", "port": p1, "emails": "me@example.com"}, J())
A.a_door_add({"name": "Models", "sub": "models", "port": p2, "emails": "me@example.com, b@example.org"}, J())
st = A.load_state()
d0, d1 = st["doors"]
key = A.a_device_add({"id": d1["id"], "name": "Laptop"}, J())
apps_before = set(cf_state()["apps"])

ok("change_zone" in A.ACTIONS, "there is a 'change_zone' action")
fn = A.ACTIONS.get("change_zone", (None, lambda a, j: (_ for _ in ()).throw(A.StepError("missing"))))[1]

e = expect_error(fn, {"zone_id": "z1"}); ok(e is not None, "refuses moving to the domain already in use")
e = expect_error(fn, {"zone_id": "nope"}); ok(e is not None, "refuses a domain the key can't see")

# a name already taken on the new domain: nothing may change at all
cf_set(dns={"z1": cf_state()["dns"]["z1"], "z2": [{"id": "x", "type": "A", "name": "chat.example.net", "content": "1.2.3.4"}]})
e = expect_error(fn, {"zone_id": "z2"})
c = cf_state(); s2 = A.load_state()
ok(e is not None and "chat.example.net" in (e or ""), "a taken name on the new domain is refused, naming it")
ok(s2["zone"] == "example.com" and [d["host"] for d in s2["doors"]] == ["chat.example.com", "models.example.com"]
   and len(c["dns"]["z2"]) == 1 and all(a["domain"].endswith("example.com") for a in c["apps"].values()),
   "after a refused move nothing changed (doors, addresses, sign-in rules)")
cf_set(dns={"z1": cf_state()["dns"]["z1"], "z2": []})

fn({"zone_id": "z2"}, J())
c = cf_state(); s3 = A.load_state(); tid = s3["tunnel_id"]
ok(s3["zone"] == "example.net" and s3["zone_id"] == "z2", "the domain is now example.net")
ok([d["host"] for d in s3["doors"]] == ["chat.example.net", "models.example.net"], "doors keep their short names on the new domain")
ok(set(c["apps"]) == apps_before and sorted(a["domain"] for a in c["apps"].values()) == ["chat.example.net", "models.example.net"],
   "the same sign-in rules were moved (people lists and device keys keep working)")
ok(any(p["decision"] == "non_identity" for p in c["policies"][d1["app_id"]].values()), "the device key's rule is still there")
hosts = [r.get("hostname") for r in c["configs"][tid]["ingress"] if r.get("hostname")]
ok(sorted(hosts) == ["chat.example.net", "models.example.net"], "routes point the new addresses at the tools")
new = {r["name"] for r in c["dns"]["z2"] if r["type"] == "CNAME" and r["proxied"] and r["content"] == f"{tid}.cfargotunnel.com"}
ok(new == {"chat.example.net", "models.example.net"}, "new addresses created on the new domain, through Cloudflare")
ok(not [r for r in c["dns"]["z1"] if r["name"] in ("chat.example.com", "models.example.com")], "old addresses removed")
ok("Move" in open(A.__file__.replace("actions.py", "ui/app.js")).read() and "change_zone" in open(A.__file__.replace("actions.py", "ui/app.js")).read(),
   "the Set up screen offers the move (button wired to change_zone)")
finish()
