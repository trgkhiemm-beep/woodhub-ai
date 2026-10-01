"""
Chạy Agent Evaluation trên dữ liệu thật.

    python -m tests.eval.run --mode rules          # không gọi LLM
    python -m tests.eval.run --mode llm            # LLM NLU (Bedrock, theo .env) + trích xuất deterministic

Kết quả: var/eval/report-<mode>.json (+ in bảng tóm tắt).
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from dotenv import dotenv_values

from app.audit import MemoryAuditSink
from tests.conftest import build_live, principal
from tests.eval.harness import Evaluator, load_cases, summarize


async def main(mode: str, only: str | None, suite: str = "cases") -> dict:
    llm_client = None
    overrides = {"NLU_MODE": "rules"}
    if mode == "llm":
        from app.nlu.llm import BedrockConverseClient

        env = dotenv_values(".env")
        llm_client = BedrockConverseClient(model_id=env["BEDROCK_MODEL_ID"], region=env.get("AWS_DEFAULT_REGION") or "us-east-1",
                                           access_key=env.get("AWS_ACCESS_KEY_ID"), secret_key=env.get("AWS_SECRET_ACCESS_KEY"),
                                           timeout=20, max_tokens=300)
        overrides = {"NLU_MODE": "llm", "BEDROCK_MODEL_ID": env["BEDROCK_MODEL_ID"]}
    container, intercept = build_live(MemoryAuditSink(), llm_client=llm_client, **overrides)
    ev = Evaluator(container.agent, intercept, principal, backend=container.backend, llm=llm_client)
    rows = []
    for case in load_cases(suite):
        if only and not case["id"].startswith(only):
            continue
        rows.append(await ev.run_case(case))
    report = summarize(rows, mode)
    report["model"] = overrides.get("BEDROCK_MODEL_ID")
    out = Path("var/eval")
    out.mkdir(parents=True, exist_ok=True)
    (out / f"report-{suite}-{mode}.json").write_text(json.dumps({"summary": report, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["rules", "llm"], default="rules")
    ap.add_argument("--only", default=None, help="chỉ chạy case có id bắt đầu bằng chuỗi này")
    ap.add_argument("--suite", choices=["cases", "holdout", "holdout2"], default="cases")
    args = ap.parse_args()
    rep = asyncio.run(main(args.mode, args.only, args.suite))
    for k, v in rep.items():
        if k not in ("failures",):
            print(f"{k:32} {v}")
    print(f"\nFAILURES ({len(rep['failures'])}):")
    for f in rep["failures"]:
        print(f"- {f['id']}: {f['input']!r} failed={f['failed']} intents={f['intents']} tools={f['tools']}")
