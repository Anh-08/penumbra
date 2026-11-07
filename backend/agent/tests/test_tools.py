"""T06: function-calling schemas for the 8 agent tools match CONTRACTS.md exactly."""
import json

import contracts as c
from backend.agent.tools import TOOL_SCHEMAS, tool_schema

# CONTRACTS.md "Agent tools": exact names and parameters, in order.
CONTRACT_TOOLS = {
    "get_fleet_status": [],
    "get_telemetry": ["sat_id", "t_from", "t_to"],
    "score_hypotheses": ["sat_id"],
    "propose_diagnostic_test": ["sat_id", "commands"],
    "propose_plan": ["sat_id", "commands"],
    "simulate": ["plan_id", "hypotheses", "monte_carlo"],
    "schedule_uplink": ["plan_id"],
    "request_approval": ["plan_id"],
}


def test_exactly_the_eight_contract_tools_in_order():
    names = [s["name"] for s in TOOL_SCHEMAS]
    assert names == list(c.AGENT_TOOLS) == list(CONTRACT_TOOLS)


def test_parameter_names_match_contract():
    for s in TOOL_SCHEMAS:
        params = s["parameters"]
        assert params["type"] == "object"
        assert list(params["properties"]) == CONTRACT_TOOLS[s["name"]], s["name"]
        assert set(params.get("required", [])) <= set(params["properties"])
        assert s["description"]


def test_required_parameters():
    req = {s["name"]: s["parameters"].get("required", []) for s in TOOL_SCHEMAS}
    assert req["get_fleet_status"] == []
    assert req["get_telemetry"] == ["sat_id"]
    assert req["score_hypotheses"] == ["sat_id"]
    assert req["propose_plan"] == ["sat_id", "commands"]
    assert req["propose_diagnostic_test"] == ["sat_id", "commands"]
    assert req["simulate"] == ["plan_id"]
    assert req["schedule_uplink"] == ["plan_id"]
    assert req["request_approval"] == ["plan_id"]


def test_enums_come_from_contracts():
    sat = tool_schema("score_hypotheses")["parameters"]["properties"]["sat_id"]
    assert sat["enum"] == list(c.SAT_IDS)
    cmd = tool_schema("propose_plan")["parameters"]["properties"]["commands"]["items"]
    assert cmd["properties"]["name"]["enum"] == list(c.COMMAND_NAMES)
    args = cmd["properties"]["args"]["properties"]
    assert set(args) == {a for names in c.COMMAND_ARGS.values() for a in names}
    assert args["load"]["enum"] == list(c.LOADS)
    assert (args["duty"]["minimum"], args["duty"]["maximum"]) == c.PAYLOAD_DUTY_RANGE
    assert (args["setpoint_c"]["minimum"], args["setpoint_c"]["maximum"]) == c.HEATER_SETPOINT_RANGE_C
    hyp = tool_schema("simulate")["parameters"]["properties"]["hypotheses"]
    array_branch = [b for b in hyp["anyOf"] if b["type"] == "array"][0]
    assert array_branch["items"]["enum"] == list(c.HYPOTHESES)


def test_diagnostic_and_plan_share_the_command_schema():
    a = tool_schema("propose_plan")["parameters"]["properties"]["commands"]
    b = tool_schema("propose_diagnostic_test")["parameters"]["properties"]["commands"]
    assert a == b


def test_schemas_are_plain_json():
    json.dumps(TOOL_SCHEMAS, allow_nan=False)
