"""Stand-in for Cloudflare's API, enough for Reach's tests. Records every call and checks request shapes.
Test-only controls: GET /__state, POST /__set."""
import json, re, sys, threading, uuid, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
ALL = {"tunnel", "apps", "tokens", "idps", "zones"}
S = {"tokens": {"good": ALL, "notokens": ALL - {"tokens"}, "noacclist": ALL}, "acc_list_tokens": ["good", "notokens"], "org": True, "reject_access": False,
     "zones": [{"id": "z1", "name": "example.com", "account": {"id": "acc1", "name": "Test account"}}, {"id": "z2", "name": "example.net", "account": {"id": "acc1", "name": "Test account"}}], "tunnels": {}, "configs": {}, "apps": {}, "policies": {},
     "svc": {}, "idps": [], "dns": {"z1": [{"id": "pre", "type": "A", "name": "taken.example.com", "content": "1.2.3.4"}], "z2": []},
     "calls": [], "kc": os.environ.get("FAKE_KC", ""), "fail_policy": False}
def ok(r=None): return 200, {"success": True, "errors": [], "result": r}
def bad(code, msg, http=400): return http, {"success": False, "errors": [{"code": code, "message": msg}]}
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _go(self, m):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"null") if n else None
        tok = (self.headers.get("Authorization") or "").replace("Bearer ", "")
        path = self.path
        S["calls"].append((m, path.split("?")[0], body))
        code, out = route(m, path, body, tok)
        data = json.dumps(out).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(data)
    do_GET = lambda s: s._go("GET"); do_POST = lambda s: s._go("POST"); do_PUT = lambda s: s._go("PUT"); do_DELETE = lambda s: s._go("DELETE")
def need(tok, perm):
    return perm in S["tokens"].get(tok, set())
def route(m, path, body, tok):
    p = path.split("?")[0]; q = path.split("?")[1] if "?" in path else ""
    if p == "/__state": return 200, {k: v for k, v in S.items() if k != "tokens"} | {"tokens": {k: sorted(v) for k, v in S["tokens"].items()}}
    if p == "/__set": S.update(body); return 200, {}
    if tok not in S["tokens"]: return bad(1000, "Invalid API Token", 401)
    if p == "/user/tokens/verify": return ok({"status": "active"})
    if p == "/accounts": return ok([{"id": "acc1", "name": "Test account"}] if tok in S["acc_list_tokens"] else [])
    mm = re.match(r"^/accounts/acc1/(.*)$", p)
    if mm:
        rest = mm.group(1)
        if rest == "access/organizations":
            return ok({"auth_domain": "myteam.cloudflareaccess.com"}) if S["org"] else bad(12130, "access.api.error.not_found: organization not found", 404)
        if rest.startswith("access/") and not S["org"]: return bad(12130, "organization not found", 404)
        if rest.startswith("cfd_tunnel"):
            if not need(tok, "tunnel"): return bad(10000, "Authentication error", 403)
            parts = rest.split("/")
            if len(parts) == 1:
                if m == "GET": return ok(list(S["tunnels"].values()))
                assert body["config_src"] == "cloudflare" and body["name"]
                tid = str(uuid.uuid4()); S["tunnels"][tid] = {"id": tid, "name": body["name"], "token": "TUNNELSECRET-" + tid[:6]}
                return ok({"id": tid, "name": body["name"], "token": S["tunnels"][tid]["token"]})
            tid = parts[1]
            if tid not in S["tunnels"]: return bad(1003, "tunnel not found", 404)
            if len(parts) == 2:
                up = os.path.exists(os.path.join(S["kc"], "loaded"))
                return ok({"id": tid, "status": "healthy" if up else "down", "connections": [{"id": "c"}] if up else []})
            if parts[2] == "token": return ok(S["tunnels"][tid]["token"])
            if parts[2] == "configurations":
                if m == "GET": return ok({"config": S["configs"].get(tid, {})})
                ing = body["config"]["ingress"]
                assert ing[-1] == {"service": "http_status:404"}, "catch-all last"
                for r in ing[:-1]:
                    assert r["hostname"] and r["service"].startswith("http://localhost:"), r
                    acc = r.get("originRequest", {}).get("access")
                    if acc and S["reject_access"]: return bad(1056, "unknown field access")
                    if acc: assert acc["required"] is True and acc["teamName"] == "myteam" and acc["audTag"], acc
                S["configs"][tid] = body["config"]; return ok({"config": body["config"]})
        if rest.startswith("access/apps"):
            if not need(tok, "apps"): return bad(10000, "Authentication error", 403)
            parts = rest.split("/")
            if len(parts) == 2:
                if m == "GET": return ok(list(S["apps"].values()))
                assert body["type"] == "self_hosted" and "." in body["domain"] and body["session_duration"]
                aid = str(uuid.uuid4()); S["apps"][aid] = dict(body, id=aid, aud="aud-" + aid[:8]); S["policies"][aid] = {}
                return ok(S["apps"][aid])
            aid = parts[2]
            if aid not in S["apps"]: return bad(12000, "app not found", 404)
            if len(parts) == 3 and m == "DELETE": S["apps"].pop(aid); S["policies"].pop(aid, None); return ok({"id": aid})
            if len(parts) == 3 and m == "PUT":
                assert body["type"] == "self_hosted" and "." in body["domain"]
                S["apps"][aid] = dict(S["apps"][aid], **body); return ok(S["apps"][aid])
            if len(parts) == 3 and m == "GET": return ok(S["apps"][aid])
            if parts[3] == "policies":
                if m == "POST":
                    if S["fail_policy"]: return bad(12001, "bad policy")
                    assert body["decision"] in ("allow", "non_identity") and body["include"]
                    for inc in body["include"]:
                        assert ("email" in inc and "@" in inc["email"]["email"]) or ("service_token" in inc and inc["service_token"]["token_id"] in S["svc"]), inc
                    precs = [x["precedence"] for x in S["policies"][aid].values()]
                    if body["precedence"] in precs: return bad(12002, "precedence must be unique")
                    pid = str(uuid.uuid4()); S["policies"][aid][pid] = dict(body, id=pid); return ok(S["policies"][aid][pid])
                pid = parts[4]
                if pid not in S["policies"][aid]: return bad(12003, "policy not found", 404)
                if m == "PUT": S["policies"][aid][pid] = dict(body, id=pid); return ok(S["policies"][aid][pid])
                if m == "DELETE": S["policies"][aid].pop(pid); return ok({"id": pid})
        if rest.startswith("access/service_tokens"):
            if not need(tok, "tokens"): return bad(10000, "Authentication error", 403)
            parts = rest.split("/")
            if len(parts) == 2:
                if m == "GET": return ok([{"id": k, "name": v["name"]} for k, v in S["svc"].items()])
                sid = str(uuid.uuid4()); S["svc"][sid] = {"name": body["name"], "duration": body.get("duration")}
                return ok({"id": sid, "name": body["name"], "client_id": f"cid-{sid[:6]}.access", "client_secret": "CLIENTSECRET-" + sid[:10]})
            if m == "DELETE": S["svc"].pop(parts[2], None); return ok({"id": parts[2]})
        if rest == "access/identity_providers":
            if not need(tok, "idps"): return bad(10000, "Authentication error", 403)
            if m == "GET": return ok(S["idps"])
            assert body["type"] == "onetimepin"; S["idps"].append(dict(body, id="otp")); return ok(S["idps"][-1])
    if p == "/zones":
        if not need(tok, "zones"): return bad(10000, "Authentication error", 403)
        return ok(S["zones"])
    mm = re.match(r"^/zones/(\w+)/dns_records(?:/(.+))?$", p)
    if mm:
        if not need(tok, "zones"): return bad(10000, "Authentication error", 403)
        z, rid = mm.group(1), mm.group(2); recs = S["dns"].setdefault(z, [])
        if m == "GET":
            name = dict(x.split("=") for x in q.split("&") if "=" in x).get("name")
            return ok([r for r in recs if not name or r["name"] == name])
        if m == "POST":
            assert body["type"] == "CNAME" and body["proxied"] is True and body["content"].endswith(".cfargotunnel.com")
            if any(r["name"] == body["name"] for r in recs): return bad(81053, "record already exists")
            r = dict(body, id=str(uuid.uuid4())); recs.append(r); return ok(r)
        if m == "DELETE": S["dns"][z] = [r for r in recs if r["id"] != rid]; return ok({"id": rid})
    return bad(7003, f"No route for {m} {p}", 404)
if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H); srv.serve_forever()
