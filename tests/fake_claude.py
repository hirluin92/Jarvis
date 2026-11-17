"""A stand-in for the `claude` program used by the worker tests.

The mode comes from the prompt on stdin (the worker gives programs a clean environment, so
nothing can travel in environment variables): the spec text contains `MODE=<name>`.
"""

import json
import os
import re
import sys
import time
from pathlib import Path

prompt = sys.stdin.read()
mode = (re.search(r"MODE=(\w+)", prompt) or [None, "edit"])[1]


def emit(**ev):
    print(json.dumps(ev), flush=True)


def result(text="Fatto.", error=False):
    emit(type="result", subtype="error" if error else "success", is_error=error, result=text,
         num_turns=3, total_cost_usd=0.01)  # fmt: skip


emit(type="system", subtype="init")
emit(type="rate_limit_event", rate_limit_info={"status": "allowed"})
emit(type="assistant", message={"content": []})
if mode == "edit":
    Path("feature.txt").write_text("nuova funzione\n", encoding="utf-8")
    result("Ho aggiunto feature.txt.")
elif mode == "protected":
    Path("secret").mkdir(exist_ok=True)
    Path("secret/key.txt").write_text("x\n", encoding="utf-8")
    Path("feature.txt").write_text("y\n", encoding="utf-8")
    result()
elif mode == "junk":
    Path("feature.txt").write_text("z\n", encoding="utf-8")
    Path("__pycache__").mkdir(exist_ok=True)
    Path("__pycache__/x.cpython-312.pyc").write_bytes(b"\x00")
    Path("node_modules/pkg").mkdir(parents=True, exist_ok=True)
    Path("node_modules/pkg/index.js").write_text("x", encoding="utf-8")
    result()
elif mode == "noop":
    result("Non c'era niente da fare.")
elif mode == "sleep":
    time.sleep(60)
elif mode == "manyturns":
    for _ in range(50):
        emit(type="assistant", message={"content": []})
    time.sleep(60)
elif mode == "quota":
    emit(type="rate_limit_event", rate_limit_info={"status": "rejected"})
    result("You have reached your usage limit", error=True)
elif mode == "error":
    result("qualcosa è andato storto", error=True)
elif mode == "env":
    # report what this process can see of the parent's environment
    Path("env.txt").write_text(json.dumps(sorted(os.environ)), encoding="utf-8")
    result()
