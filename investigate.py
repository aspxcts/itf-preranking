import json

with open('output/latest_points_earned.json', encoding='utf-8') as f:
    pts = json.load(f)

with open('output/latest_merged_rankings.json', encoding='utf-8') as f:
    mr = json.load(f)

with open('output/latest_player_breakdowns.json', encoding='utf-8') as f:
    bd = json.load(f)

# Build pid->points map from pointsData (boys only)
pts_by_pid = {}
for t in pts['tournaments']:
    for r in t['results']:
        pid = str(r['player_id'])
        if r['event'].startswith('B') and r['points']:
            pts_by_pid.setdefault(pid, []).append((t['name'], r['event'], r['points']))

print(f"Boys in pointsData this week: {len(pts_by_pid)}")

# Top 30 boys with pts_change=0 but found in pointsData
suspects = []
for row in mr['boys'][:30]:
    pid = str(row['player_id'])
    pchange = row.get('points_change', 0)
    in_pts = pid in pts_by_pid
    in_bd = pid in bd['players']
    if pchange == 0 and in_pts:
        suspects.append((row, pid, in_bd))

print(f"\nSuspects (rank<=30, pts_change=0, in pointsData): {len(suspects)}")
for row, pid, in_bd in suspects:
    rank = row['rank']
    name = row['name']
    print(f"  rank={rank}  name={name}  pid={pid}  in_bd={in_bd}")
    for tname, ev, p in pts_by_pid[pid]:
        print(f"    -> {tname}  {ev}  pts={p}")

# Also check girls
pts_by_pid_g = {}
for t in pts['tournaments']:
    for r in t['results']:
        pid = str(r['player_id'])
        if r['event'].startswith('G') and r['points']:
            pts_by_pid_g.setdefault(pid, []).append((t['name'], r['event'], r['points']))

suspects_g = []
for row in mr['girls'][:30]:
    pid = str(row['player_id'])
    pchange = row.get('points_change', 0)
    in_pts = pid in pts_by_pid_g
    in_bd = pid in bd['players']
    if pchange == 0 and in_pts:
        suspects_g.append((row, pid, in_bd))

print(f"\nGirls suspects (rank<=30, pts_change=0, in pointsData): {len(suspects_g)}")
for row, pid, in_bd in suspects_g:
    rank = row['rank']
    name = row['name']
    print(f"  rank={rank}  name={name}  pid={pid}  in_bd={in_bd}")
    for tname, ev, p in pts_by_pid_g[pid]:
        print(f"    -> {tname}  {ev}  pts={p}")
