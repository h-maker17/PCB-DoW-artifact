"""Frozen cache-health feature definitions; excludes each round's cold replica."""
import statistics,math
HEALTH_FLOOR=.95
def features(prompt,cached):
 assert len(prompt)==len(cached)>=2 and cached[0]==0
 ratios=[c/p for p,c in zip(prompt[1:],cached[1:])]
 assert all(math.isfinite(h) and 0<=h<=1 for h in ratios)
 return {'mean_hit':statistics.mean(ratios),'variance_hit':statistics.pvariance(ratios),'min_hit':min(ratios),'below_floor_count':sum(h<HEALTH_FLOOR for h in ratios),'health_floor':HEALTH_FLOOR,'reusing_replicas':len(ratios)}
