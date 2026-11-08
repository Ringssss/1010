"""Verify failed bootstrap evidence is durable without invoking SSH or Docker."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

def main():
    source=Path(__file__).with_name("fresh_container.py")
    loader=importlib.util.spec_from_file_location("fresh_controller_under_test",source)
    module=importlib.util.module_from_spec(loader);loader.loader.exec_module(module)
    root=module.ROOT/"codex_coding/results/v0.1.23.3/task2/three_node_recovery/inventory_controller_cpu_r01"
    root.mkdir(parents=True,exist_ok=False)
    spec=module.ROOT/"codex_coding/results/v0.1.23.3/task2/three_node_recovery/inventory_setup_plan_r06/node28.spec.json"
    cases=[]
    for name,retval,error,expected in (
        ("rejected",subprocess.CompletedProcess([],255,b"",b"synthetic SSH refusal"),None,"completed"),
        ("timeout",None,subprocess.TimeoutExpired(["ssh"],8,output=b"partial",stderr=b"synthetic deadline"),"timeout"),
    ):
        out=root/name
        argv=[str(source),"--spec",str(spec),"--out",str(out),"--mode","setup","--execute"]
        with patch.object(sys,"argv",argv),patch.object(module.subprocess,"run",return_value=retval,side_effect=error) as mocked:
            try:module.main()
            except (RuntimeError,subprocess.TimeoutExpired):pass
            else:raise AssertionError("expected failed bootstrap")
            assert mocked.call_count==1
        record=json.loads((out/"command.json").read_text());result=json.loads((out/"result.json").read_text())
        assert len(record["commands"])==1
        assert record["commands"][0]["status"]==expected
        assert "synthetic" in record["commands"][0]["stderr"]
        assert record["commands"][0]["ended_utc"]
        assert result["status"] in ("bootstrap_failed","timeout")
        cases.append({"case":name,"passed":True,"host_calls":0})
    (root/"summary.json").write_text(json.dumps({"cases":cases,"source_sha256":hashlib.sha256(source.read_bytes()).hexdigest()},indent=2)+"\n")
    print(json.dumps(cases))

if __name__=="__main__":main()
