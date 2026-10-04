import sys
sys.path.insert(0, r"D:\repo3\CellSmith")

# 1. Verify protocol
from cellsmith.adapters.base import BaseWorkflowAdapter, DagNode, DagGraph, Metadata
print("✓ base.py: all classes defined correctly")
print(f"  - DagNode fields: {list(DagNode.__dataclass_fields__.keys())}")

# 2. Verify LogicAppAdapter
from cellsmith.adapters.logic_app import LogicAppAdapter
adapter = LogicAppAdapter()
print(f"\n✓ LogicAppAdapter:")
print(f"  - name: {repr(adapter.name)}")
print(f"  - target_format: {repr(adapter.target_format)}")

# 3. Verify unpack() with interning
sample = {
    "definition": {
        "$schema": "https://schemas.microsoft.com/logicapps/2019-04-01/schema",
        "contentVersion": "1.5.0.0",
        "parameters": [],
        "triggers": [{"type": "ScheduledTrigger"}],
        "actions": {
            "Get_SignInLogs": {
                "type": "ApiConnection",
                "actions": {"body": "@body('Get_SignInLogs')['Body'] | where IPAddress == _ip"}
            },
            "Filter_HighRisk": {
                "type": "If",
                "actions": {"body": "@body('Get_SignInLogs')['Body'] | where Score > 10"},
                "else": {"actions": {"body": "@body('Get_SignInLogs')['Body'] | take 10"}}
            },
            "Send_Alert": {
                "type": "Http",
                "actions": {"body": "@body('Filter_HighRisk')['Body']"}
            }
        },
        "outputs": {"HighRiskLogins": "@body('Filter_HighRisk')['Body']"}
    }
}

# Test with interning
graph, meta = adapter.unpack(sample)
print(f"\n✓ unpack() works: {len(graph.nodes)} nodes, interning stats = {meta.get('stats')}")

# Test pack
result = adapter.pack(graph)
print(f"\n✓ pack() works: result keys = {list(result.keys())}")

# 4. Verify registry
from cellsmith.registry import load_all_adapters, get_adapter
adaptors = load_all_adapters()
print(f"\n✓ Registry discovered {len(adaptors)} adapter(s)")

found = get_adapter("logic_app")
print(f"✓ get_adapter('logic_app') -> {found.name}")

print("\nAll tests passed")
