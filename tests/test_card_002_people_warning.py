"""Card 002 · Warn before the free plan's 50-person limit. Acceptance test: agents must not edit this file."""
from harness import A, ok, J, finish, connect, tool_port

connect("z1")
p = tool_port()
first = ", ".join(f"p{i}@example.com" for i in range(30))
second = ", ".join(f"p{i}@example.com" for i in range(20, 40))       # 10 overlap with the first door
A.a_door_add({"name": "One", "sub": "one", "port": p, "emails": first}, J())
A.a_door_add({"name": "Two", "sub": "two", "port": p, "emails": second}, J())
s = A.status()
ok(s.get("people") == 40, f"status counts each person once across doors (got {s.get('people')}, want 40)")
ok(s.get("people_warning") is False, "no warning at 40 people")
d = A.load_state()["doors"][1]
A.a_door_people({"id": d["id"], "emails": second + ", extra@example.com"}, J())
s = A.status()
ok(s.get("people") == 41 and s.get("people_warning") is True, "warning on at 41 people")
ui = open(A.__file__.replace("actions.py", "ui/app.js")).read()
ok("people_warning" in ui and "50" in ui, "the Doors screen shows the warning and mentions the 50-person limit")
finish()
