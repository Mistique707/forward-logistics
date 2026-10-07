"""Route and load planner: one OR-Tools vehicle-routing model across transport modes.

Every demand chunk becomes one node per feasible mode (truck / helicopter / airdrop); a
disjunction lets the solver serve it by exactly one mode or drop it at a penalty. Trucks carry
the road route (plus a mule/porter last leg to track posts); helicopters and airdrop aircraft are
modelled as one vehicle per sortie per day, so grounding a fleet just removes sorties. Pass
closures and weather no-go days carve forbidden intervals out of the arrival time windows.
"""
import math

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from . import network
from .config import CLASSES, MODES, PLAN_DAYS, heli_derate

H = PLAN_DAYS * 24
URGENT_PENALTY = 10 ** 9
EMERGENCY_H = 24  # stock-outs: anything that can land within a day
LATE_PENALTY = URGENT_PENALTY // 4  # serving a load after its runout beats not serving it, but only just
LATE_PER_HOUR = 5000  # ...and the earlier the better (a day late costs more than a spare sortie)
MAX_NODES = 300
TIME_LIMIT_S = 5  # hard cap for the whole plan, split across the strategies
STRATEGIES = (routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION,
              routing_enums_pb2.FirstSolutionStrategy.ALL_UNPERFORMED)


def _chunks(demands, urgent_kg, stock_kg):
    out = []
    for d in demands:
        for kind, size in (("urgent", urgent_kg), ("stocking", stock_kg)):
            kg = d[f"{kind}_kg"]
            n = math.ceil(kg / size - 1e-9) if kg > 1 else 0
            out += [{**d, "kind": kind, "kg": kg / n} for _ in range(n)]
    return out


def _mule_days(kg):
    return math.ceil(kg / MODES["mule"]["payload_kg"] - 1e-9)


def air_cost_per_kg(post):
    """What a kg costs to fly in by helicopter later (sortie cost spread over the derated payload)."""
    h = MODES["heli"]
    alt = network.load()["by_id"][post]["alt_m"]
    return (h["fixed"] + 2 * network.air_km(h["base"], post) * h["per_km"]) / (h["payload_kg"] * heli_derate(alt))


def _hours(km, mode):
    return km / MODES[mode]["speed_kmh"]


def plan(demands, ctx):
    """demands: [{post, cls, urgent_kg, stocking_kg, runout_day}] (runout_day None = beyond horizon)
    ctx: closures {pass: [(start_h, end_h)]}, no_go {mode: {post: {day}}}, grounded {mode: {day}},
         mule_blocked {post: {day}}, trucks (int)."""
    net = network.load()
    by_id = net["by_id"]
    urgent_kg, stock_kg = 500, 2000
    while True:  # coarsen chunks until the model is small enough to solve in seconds
        chunks = _chunks(demands, urgent_kg, stock_kg)
        if sum(3 if c["kind"] == "urgent" else 1 for c in chunks) + 3 <= MAX_NODES:
            break
        urgent_kg, stock_kg = urgent_kg * 2, stock_kg * 2

    # ---- nodes: 0 SAPPHIRE, 1 ONYX, 2 END, then (chunk, mode) options ----
    nodes = [{"post": "SAPPHIRE"}, {"post": "ONYX"}, {"post": "END"}]
    disj = []
    urgent_mule_kg = {}
    for c in chunks:
        if c["kind"] == "urgent" and by_id[c["post"]]["access"] == "track":
            urgent_mule_kg[c["post"]] = urgent_mule_kg.get(c["post"], 0) + c["kg"]
    def options(c, deadline):
        post = by_id[c["post"]]
        opts = []
        # truck (+ mule last leg)
        road = network.pair("SAPPHIRE", c["post"])
        t_arrive = _hours(road["km"], "truck")
        hi = deadline
        if post["access"] == "track" and c["kind"] == "urgent":
            blocked = len([d for d in ctx["mule_blocked"].get(c["post"], ()) if d * 24 < deadline])
            hi -= 24 * (_mule_days(urgent_mule_kg[c["post"]]) + blocked)
        forbid = []
        for p, km_at in road["passes"].items():
            after = _hours(road["km"] - km_at, "truck")
            for a, b in ctx["closures"].get(p, ()):
                if b >= H:  # seasonal closure: must cross before it starts
                    hi = min(hi, a + after)
                else:
                    forbid.append((math.floor(a + after), math.ceil(b + after)))
        if hi >= t_arrive:
            opts.append({"mode": "truck", "lo": math.ceil(t_arrive), "hi": int(hi), "forbid": forbid,
                         "load": round(c["kg"])})
        # air modes, only for what would otherwise run out inside the window
        for mode in ("heli", "airdrop") if c["kind"] == "urgent" else ():
            m = MODES[mode]
            if mode == "heli" and network.air_km(m["base"], c["post"]) > m["range_km"]:
                continue
            no_go = ctx["no_go"].get(mode, {}).get(c["post"], set()) | ctx["grounded"].get(mode, set())
            forbid = [(24 * d, 24 * d + 24) for d in sorted(no_go) if 24 * d < deadline]
            if all(24 * d in [f[0] for f in forbid] for d in range(math.ceil(deadline / 24))):
                continue  # no flyable day before the deadline
            factor = heli_derate(post["alt_m"]) if mode == "heli" else 1 - m["loss"]
            opts.append({"mode": mode, "lo": 0, "hi": int(deadline), "forbid": forbid,
                         "load": math.ceil(c["kg"] / factor)})
        return opts

    for ci, c in enumerate(chunks):
        # urgent loads must land a day before the runout day; stocking loads anywhere in the window. A post that is
        # already out, or runs out tomorrow, gets the fastest lift that can land within EMERGENCY_H.
        deadline = H if c["kind"] == "stocking" or c["runout_day"] is None else min(
            H, max(EMERGENCY_H, (c["runout_day"] - 1) * 24))
        opts = options(c, deadline)
        if not opts and c["kind"] == "urgent" and deadline < H:
            # nothing lands in time: still plan the earliest lift that can, and flag it as late
            opts = [{**o, "late": True} for o in options(c, H)]
        idx = []
        for o in opts:
            idx.append(len(nodes))
            nodes.append({"post": c["post"], "chunk": ci, **o})
        disj.append(idx)

    # ---- vehicles ----
    vehicles = []
    for i in range(ctx.get("trucks", MODES["truck"]["fleet"])):
        vehicles.append({"name": f"TRK-{i + 1:02d}", "mode": "truck", "start": 0, "win": (0, H), "end_max": H + 48})
    for mode, start in (("heli", 1), ("airdrop", 0)):
        m = MODES[mode]
        frames = m.get("airframes", 1)
        for d in range(PLAN_DAYS):
            if d in ctx["grounded"].get(mode, set()):
                continue
            for a in range(frames):
                for s in range(m["sorties_per_day"]):
                    t0 = 24 * d + 7 + 4 * s
                    tag = (f"HEL-{chr(65 + a)}{d + 1:02d}/{s + 1}" if mode == "heli" else f"AD-{d + 1:02d}")
                    vehicles.append({"name": tag, "mode": mode, "start": start, "win": (t0, t0 + 2),
                                     "end_max": 24 * d + 17})

    # Cheapest insertion is fast but can lock a chunk into the wrong mode; starting from nothing
    # performed lets make-active moves weigh every mode. Both are deterministic; keep the cheaper.
    runs = [r for r in (_solve(nodes, vehicles, chunks, disj, st) for st in STRATEGIES) if r]
    if not runs:
        raise RuntimeError("planner found no solution")
    return min(runs, key=lambda r: r[0])[1]


def _solve(nodes, vehicles, chunks, disj, strategy):
    by_id = network.load()["by_id"]
    mgr = pywrapcp.RoutingIndexManager(len(nodes), len(vehicles), [v["start"] for v in vehicles],
                                       [2] * len(vehicles))
    routing = pywrapcp.RoutingModel(mgr)

    places = sorted({n["post"] for n in nodes} - {"END"})
    road = {(a, b): network.pair(a, b)["km"] for a in places for b in places}
    air = {(a, b): network.air_km(a, b) if a != b else 0.0 for a in places for b in places}
    service = {"truck": 1, "heli": 0.5, "airdrop": 0.25}
    mule_cost = [MODES["mule"]["per_kg"] * n["load"] if i > 2 and by_id[n["post"]]["access"] == "track" else 0
                 for i, n in enumerate(nodes)]
    late_cost = [LATE_PENALTY if n.get("late") else 0 for n in nodes]
    cost_cb, time_cb = {}, {}
    for mode in ("truck", "heli", "airdrop"):
        m, d = MODES[mode], road if mode == "truck" else air
        dist = [[0.0 if "END" in (a["post"], b["post"]) else d[(a["post"], b["post"])] for b in nodes] for a in nodes]
        start = 1 if mode == "heli" else 0
        # the fixed sortie cost sits on the first leg so insertion heuristics see it too
        cost = [[int(x * m["per_km"] + (mule_cost[j] if mode == "truck" else 0) + late_cost[j]
                     + (m["fixed"] if i == start and j > 2 else 0)) for j, x in enumerate(row)]
                for i, row in enumerate(dist)]
        tt = [[math.ceil(_hours(x, mode) + (service[mode] if i > 2 else 0)) for x in row] for i, row in enumerate(dist)]
        cost_cb[mode] = routing.RegisterTransitMatrix(cost)
        time_cb[mode] = routing.RegisterTransitMatrix(tt)
    load_cb = routing.RegisterUnaryTransitVector([n.get("load", 0) for n in nodes])

    for v, veh in enumerate(vehicles):
        routing.SetArcCostEvaluatorOfVehicle(cost_cb[veh["mode"]], v)
    routing.AddDimensionWithVehicleCapacity(load_cb, 0, [MODES[v["mode"]]["payload_kg"] for v in vehicles],
                                            True, "load")
    routing.AddDimensionWithVehicleTransits([time_cb[v["mode"]] for v in vehicles], H, H + 48, False, "time")
    tdim = routing.GetDimensionOrDie("time")

    for n_i in range(3, len(nodes)):
        n = nodes[n_i]
        ix = mgr.NodeToIndex(n_i)
        tdim.CumulVar(ix).SetRange(n["lo"], n["hi"])
        for a, b in n["forbid"]:
            if a < b:
                tdim.CumulVar(ix).RemoveInterval(a, b - 1)
        routing.VehicleVar(ix).SetValues([-1] + [v for v, veh in enumerate(vehicles) if veh["mode"] == n["mode"]])
        if n.get("late"):
            tdim.SetCumulVarSoftUpperBound(ix, 0, LATE_PER_HOUR)
    for ci, idx in enumerate(disj):
        c = chunks[ci]
        if c["kind"] == "urgent":
            penalty = int(URGENT_PENALTY * CLASSES[c["cls"]]["crit"])
        else:  # deferring stocking means flying it in later
            penalty = int(air_cost_per_kg(c["post"]) * c["kg"])
        if idx:
            routing.AddDisjunction([mgr.NodeToIndex(i) for i in idx], penalty, 1)
    for v, veh in enumerate(vehicles):
        tdim.CumulVar(routing.Start(v)).SetRange(*veh["win"])
        tdim.CumulVar(routing.End(v)).SetMax(veh["end_max"])

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = strategy
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GREEDY_DESCENT
    params.time_limit.FromMilliseconds(int(TIME_LIMIT_S * 1000 / len(STRATEGIES)))
    sol = routing.SolveWithParameters(params)
    if sol is None:
        return None
    return sol.ObjectiveValue(), _extract(sol, routing, mgr, tdim, nodes, vehicles, chunks, disj)


def _extract(sol, routing, mgr, tdim, nodes, vehicles, chunks, disj):
    by_id = network.load()["by_id"]
    trips, served = [], set()
    for v, veh in enumerate(vehicles):
        ix = sol.Value(routing.NextVar(routing.Start(v)))
        if routing.IsEnd(ix):
            continue
        stops = {}
        order = []
        dep = sol.Min(tdim.CumulVar(routing.Start(v)))
        latest = veh["win"][1]  # latest departure that still meets every stop's window
        while not routing.IsEnd(ix):
            n = nodes[mgr.IndexToNode(ix)]
            latest = min(latest, dep + n["hi"] - sol.Min(tdim.CumulVar(ix)))
            c = chunks[n["chunk"]]
            served.add(n["chunk"])
            if n["post"] not in stops:
                order.append(n["post"])
                stops[n["post"]] = {"post": n["post"], "arrive_h": sol.Min(tdim.CumulVar(ix)), "items": {}, "kg": 0.0,
                                    "mule": veh["mode"] == "truck" and by_id[n["post"]]["access"] == "track",
                                    "late": []}
            s = stops[n["post"]]
            if n.get("late") and c["cls"] not in s["late"]:
                s["late"].append(c["cls"])
            s["items"][c["cls"]] = s["items"].get(c["cls"], 0) + c["kg"] / CLASSES[c["cls"]]["kg"]
            s["kg"] += c["kg"]
            ix = sol.Value(routing.NextVar(ix))
        mode = veh["mode"]
        origin = MODES[mode]["base"]
        legs = [origin] + order
        if mode == "truck":
            km = sum(network.pair(a, b)["km"] for a, b in zip(legs, legs[1:]))
            geom = [pt for a, b in zip(legs, legs[1:]) for pt in network.pair(a, b)["geom"]]
            passes = sorted({p for a, b in zip(legs, legs[1:]) for p in network.pair(a, b)["passes"]})
        else:
            km = sum(network.air_km(a, b) for a, b in zip(legs, legs[1:]))
            geom = [[by_id[x]["lat"], by_id[x]["lon"]] for x in legs]
            passes = []
        m = MODES[mode]
        load = sum(s["kg"] for s in stops.values())
        trips.append({
            "vehicle": veh["name"], "mode": mode, "origin": origin,
            "depart_h": dep,
            "depart_by_h": latest,
            "stops": [{**stops[p], "arrive_by_h": stops[p]["arrive_h"] + latest - dep,
                       "items": {k: round(q) for k, q in stops[p]["items"].items()},
                       "kg": round(stops[p]["kg"])} for p in order],
            "km": round(km), "passes": passes, "load_kg": round(load),
            "capacity_kg": m["payload_kg"], "cost": round(m["fixed"] + km * m["per_km"]
                                                          + (MODES["mule"]["per_kg"] * sum(
                                                              s["kg"] for s in stops.values() if s["mule"]))),
            "geom": geom})
    trips.sort(key=lambda t: (t["depart_h"], t["vehicle"]))
    unmet, deferred = [], []
    for ci, c in enumerate(chunks):
        if ci in served:
            continue
        (unmet if c["kind"] == "urgent" else deferred).append(
            {"post": c["post"], "cls": c["cls"], "kg": round(c["kg"]), "runout_day": c["runout_day"],
             "air_cost": round(air_cost_per_kg(c["post"]) * c["kg"]),
             "reason": "no feasible mode before runout" if not disj[ci] else "not served in time"})
    return {"trips": trips, "unmet": _merge(unmet), "deferred": _merge(deferred), "totals": _totals(trips, unmet,
                                                                                                  deferred),
            "nodes": len(nodes)}


def _merge(rows):
    out = {}
    for r in rows:
        k = (r["post"], r["cls"])
        m = out.setdefault(k, {**r, "kg": 0, "air_cost": 0})
        m["kg"] += r["kg"]
        m["air_cost"] += r["air_cost"]
    return list(out.values())


def _totals(trips, unmet, deferred):
    t = {"cost": sum(x["cost"] for x in trips), "unmet_kg": sum(x["kg"] for x in unmet),
         "deferred_kg": sum(x["kg"] for x in deferred), "deferred_cost": sum(x["air_cost"] for x in deferred),
         "by_mode": {}}
    for x in trips:
        m = t["by_mode"].setdefault(x["mode"], {"trips": 0, "kg": 0, "cost": 0})
        m["trips"] += 1
        m["kg"] += x["load_kg"]
        m["cost"] += x["cost"]
    return t
