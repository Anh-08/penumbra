"""Peer comparison (CONTRACTS.md): is it the environment (fleet-wide) or this satellite's hardware?

Built on the fleet-coupled scoring from T16: beta season (H5) is one fleet event, so it only wins
when the peers show the same array deficit. env_score = P(H5); hw_score = P(H1..H4).
"""
from __future__ import annotations

from contracts import PeerCompare
from backend.verify import constants as K
from backend.verify import scoring

HW_KEYS = {"H1": "own_array_output_low", "H2": "own_load_higher_than_commanded",
           "H3": "own_battery_capacity_low", "H4": "own_sensor_inconsistent"}
# a peer "shows a deficit" when an array-power explanation beats nominal by this much (log-likelihood)
PEER_DEFICIT_LL = 2.0


def peer_compare(evidence: dict[str, scoring.SatEvidence], sat_id: str) -> PeerCompare:
    post = scoring.score_fleet(evidence, prior=K.PRIOR)[sat_id].posterior
    env = post["H5"]
    hw = sum(post[h] for h in ("H1", "H2", "H3", "H4"))
    peers = [s for s in evidence if s != sat_id]
    deficit = 0
    for s in peers:
        ll, _ = evidence[s].hyp_loglik()
        if max(ll["H1"], ll["H5"]) - ll["H0"] > PEER_DEFICIT_LL:
            deficit += 1
    keys = []
    if peers:
        keys.append("peers_show_same_array_deficit" if deficit * 2 >= len(peers) else "peers_nominal")
    top = max(post, key=post.get)
    if top == "H5":
        keys.append("fleet_wide_environment")
    elif top in HW_KEYS:
        keys.append(HW_KEYS[top])
    else:
        keys.append("nominal")
    return PeerCompare(env_score=float(env), hw_score=float(hw), explanation_keys=keys)
