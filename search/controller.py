# -*- coding: utf-8 -*-
import re
import time
import uuid
from typing import Optional, List, Tuple

from .config import DEFAULT_CATEGORY, _envf
from .question import parse_query
from .service import hybrid_search
from .context_builder import build_context
from .prompt_template import render_prompt
from .llm_client import call_llm, LLM_MODEL
from .validator import (
    extract_verdict_symbol,
    client_safe_text,
    verdict_guard
)

DEBUG_CTRL   = _envf("DEBUG_CTRL", "1") in ("1","true","TRUE","yes","YES")
DEBUG_PRMPT  = _envf("DEBUG_PRMPT","1") in ("1","true","TRUE","yes","YES")

def generate_answer(
    question_text: str,
    category: Optional[str] = None,
    k: int = 12,
) -> str:
    req_id = str(uuid.uuid4())
    category = category or DEFAULT_CATEGORY

    t0 = time.time()
    parsed = parse_query(question_text)

    items = hybrid_search(
        category=category,
        query_text=question_text,
        must_ingredients=parsed.get("must_ingredients", []),
        soft_efficacies=parsed.get("soft_efficacies", []),
        must_brands=parsed.get("must_brands", []),
        must_products=parsed.get("must_products", []),
        k=k,
    )

    parsed_for_builder = {
        "category": category,
        "brand": (parsed.get("must_brands") or [None])[0],
        "product": (parsed.get("must_products") or [None])[0],
        "ingredients": parsed.get("must_ingredients", []),
        "functions": parsed.get("soft_efficacies", []),
        "must_terms": (parsed.get("must_ingredients", [])
                       + parsed.get("soft_efficacies", [])),
    }

    candidates = []
    for it in items:
        meta = it.get("metadata") or {}
        text = (it.get("chunk") or it.get("content") or it.get("text") or "")
        candidates.append({
            "text": text,
            "scores": {
                "combined": float(it.get("score", 0.0)),
                "vector": float(it.get("vec", 0.0)),
                "fts": float(it.get("beta", 0.0)),
                "trgm": float(it.get("gamma", 0.0)),
            },
            "source_id": it.get("source_id", ""),
            "source": ("rag_documents_food"
                       if category == "functional_food"
                       else "rag_documents_cosmetic"),
            "meta": {
                "category": category,
                "brand": (meta.get("brand") or meta.get("ltd") or meta.get("company")),
                "product": (meta.get("product") or meta.get("product_name")),
                "ingredients": ([meta.get("raw_material")] if meta.get("raw_material") else []),
                "functions": (meta.get("functionalities") or meta.get("function") or []),
                "approval_type": (meta.get("approval_type") or meta.get("approval")),
                "approval_id": meta.get("approval_id"),
                "url": meta.get("url"),
                "updated_at": meta.get("updated_at"),
            },
        })

    payload, _debug = build_context(
        question_text,
        parsed_for_builder,
        candidates,
    )

    # 프롬프트 생성
    prompt = render_prompt(payload)
    if DEBUG_PRMPT:
        print("\n[CTRL] ===== prompt (first 600 chars) =====")
        print(prompt[:600])
        print("[CTRL] ====================================\n")

    # LLM 호출
    llm_answer, llm_latency_ms, model_name = call_llm(prompt)

    # 모델 판정 추출
    model_symbol = extract_verdict_symbol(llm_answer)

    # 컨트롤러 가드로 보정
    fixed_symbol = verdict_guard(question_text, payload, model_symbol)

    # 헤더
    ev_count = (
        payload.get("evidence_count")
        or len(payload.get("evidence", []))
        or payload.get("summary", {}).get("evidence_count")
        or 0
    )
    total_latency_ms = (time.time() - t0) * 1000.0
    header = (
        f"VERDICT: {fixed_symbol} | MODEL: {model_name} | "
        f"EVIDENCE: {int(ev_count)} | REQ: {req_id} | "
        f"LATENCY_MS: {int(total_latency_ms)}"
    )
    if DEBUG_CTRL:
        print(f"[CTRL] header: {header}")

    # 클라이언트 안전 문구로 정제
    llm_answer = client_safe_text(llm_answer)

    # 본문 내 잘못 표시된 verdict 아이콘이 있으면 보정(가장 첫 줄만 교체)
    llm_answer = re.sub(r"^VERDICT:\s*[✅⚠️❌]", f"VERDICT: {fixed_symbol}", llm_answer, count=1)

    return f"{header}\n{llm_answer}"
