# search/service.py
from typing import Dict, List, Tuple
from psycopg2 import sql as psql
from .config import (
    CATEGORY_TABLE,
    SEARCH_ALPHA, SEARCH_BETA, SEARCH_GAMMA, SEARCH_DELTA,
    TRGM_MIN_SIM,
)
from .db import get_conn, embed_text
from .normalize import normalize_text, collapse_spaces
from .query_builder import QueryBuilder


def table_for_category(category: str) -> Tuple[psql.Identifier, psql.Identifier]:
    if category not in CATEGORY_TABLE:
        raise ValueError("category must be 'functional_food' or 'functional_cosmetic'")
    schema, table = CATEGORY_TABLE[category]
    return psql.Identifier(schema), psql.Identifier(table)


def _dynamic_trgm_threshold(q: str, base: float = TRGM_MIN_SIM) -> float:
    """짧은 토큰은 임계값 상향, 긴 토큰은 하향 (대략적인 휴리스틱)."""
    L = max(1, len(q.replace(" ", "")))
    if L <= 4:
        return max(0.20, min(0.36, base + 0.06))   # 0.34~0.36 부근
    if L <= 7:
        return max(0.20, min(0.34, base + 0.02))   # 0.30~0.32 부근
    return max(0.18, min(0.32, base - 0.04))       # 0.24~0.28 부근


def _make_alias_patterns(terms: List[str]) -> List[str]:
    """
    ILIKE ANY용 패턴 배열 생성. 공백 있는/없는 버전 모두 포함.
    ex) '대두 배아 열수 추출물' -> '%대두 배아 열수 추출물%', '%대두배아열수추출물%'
    """
    patterns: List[str] = []
    for t in terms:
        nz = normalize_text(t)
        if not nz:
            continue
        coll = collapse_spaces(nz)
        patterns.append(f"%{nz}%")
        if coll != nz:
            patterns.append(f"%{coll}%")
    # 중복 제거(순서 유지)
    seen = set()
    return [p for p in patterns if not (p in seen or seen.add(p))]


def _safe_patterns(arr: List[str]) -> List[str]:
    """
    LIKE ANY 배열이 비었을 때, 과매칭 방지를 위한 안전 패턴.
    절대 매치되지 않을 문자열을 넣는다.
    """
    return arr if arr else ["%__never_match_token__%"]


def hybrid_search(category: str,
                  query_text: str,
                  must_ingredients: List[str] = None,
                  soft_efficacies: List[str] = None,
                  must_brands: List[str] = None,
                  must_products: List[str] = None,
                  k: int = 12) -> List[Dict]:
    """
    step1: MUST ingredient + SOFT efficacy
    step2: MUST ingredient only
    step3: no MUST (완화)
    """
    must_ingredients = must_ingredients or []
    soft_efficacies  = soft_efficacies  or []
    must_brands      = must_brands or []
    must_products    = must_products or []

    # 질의 정규화
    q_norm = normalize_text(query_text)
    q_coll = collapse_spaces(q_norm)

    # 별칭 패턴 (ingredient / brand / product)
    alias_patterns_ing   = _make_alias_patterns(must_ingredients)
    alias_patterns_brand = _make_alias_patterns(must_brands)
    alias_patterns_prod  = _make_alias_patterns(must_products)

    # 효능 질의(eff_q):
    #  - soft_efficacies가 있으면 OR 결합(" | ")
    #  - 없으면 질문 자체(q_norm)를 websearch 질의로 폴백
    if soft_efficacies:
        eff_terms = [normalize_text(e) for e in soft_efficacies if normalize_text(e)]
        eff_q = " | ".join(eff_terms) if eff_terms else ""
    else:
        eff_q = q_norm  # 폴백: 질문 자체를 효능 질의로 사용

    # 동적 trgm 임계값
    dyn_trgm = _dynamic_trgm_threshold(q_norm, TRGM_MIN_SIM)

    print("\n[hybrid] category               =", category)
    print("[hybrid] query_raw              =", query_text)
    print("[hybrid] query_norm/col         =", q_norm, "/", q_coll)
    print("[hybrid] must_ingredients       =", must_ingredients)
    print("[hybrid] alias_patterns_ing(n)  =", len(alias_patterns_ing))
    print("[hybrid] must_brands            =", must_brands)
    print("[hybrid] alias_patterns_brand(n)=", len(alias_patterns_brand))
    print("[hybrid] must_products          =", must_products)
    print("[hybrid] alias_patterns_prod(n) =", len(alias_patterns_prod))
    print("[hybrid] soft_efficacies        =", soft_efficacies)
    print("[hybrid] eff_q                  =", eff_q)
    print("[hybrid] weights α/β/γ/δ        =", SEARCH_ALPHA, SEARCH_BETA, SEARCH_GAMMA, SEARCH_DELTA)
    print("[hybrid] trgm_min_sim(base/dyn) =", TRGM_MIN_SIM, "/", dyn_trgm)

    q_vec = embed_text(q_norm)
    schema_id, table_id = table_for_category(category)

    # 백오프 단계
    steps = [
        (True,  True),   # step1: MUST ingredient + SOFT efficacy
        (True,  False),  # step2: MUST ingredient only
        (False, False),  # step3: relax all
    ]

    hits: List[Dict] = []
    with get_conn() as conn, conn.cursor() as cur:
        for sidx, (use_must_ing, use_soft_eff) in enumerate(steps, 1):
            params = {
                "qvec": q_vec,
                "qnorm": q_norm,
                "qcoll": q_coll,
                "trgm_min": dyn_trgm,
                "k": k * (1 + sidx),
                "alpha": SEARCH_ALPHA, "beta": SEARCH_BETA, "gamma": SEARCH_GAMMA, "delta": SEARCH_DELTA,
                # 배열 파라미터 (안전 패턴 적용)
                "alias_patterns_ing":   _safe_patterns(alias_patterns_ing),
                "alias_patterns_brand": _safe_patterns(alias_patterns_brand),
                "alias_patterns_prod":  _safe_patterns(alias_patterns_prod),
                "eff_q": eff_q or "",
            }

            # --- 필터 조각 (QueryBuilder 사용) ---
            ing_filter = QueryBuilder.build_ingredient_filter(must_ingredients if use_must_ing else [])
            eff_filter = QueryBuilder.build_efficacy_filter(use_soft_eff, soft_efficacies)
            
            # --- 메인 쿼리 (QueryBuilder 사용) ---
            sql_q = QueryBuilder.build_hybrid_query(schema_id, table_id, ing_filter, eff_filter)

            cur.execute(sql_q, params)
            rows = cur.fetchall()

            print(f"[hybrid] step{sidx} hits =", len(rows))
            for i, r in enumerate(rows[:5]):
                sid, preview = r[1], (r[3] or "")[:100].replace("\n", " ")
                print(f"  - {i+1:02d} src={sid} | score={r[5]:.3f} (vec={r[6]:.3f}, β={r[7]:.3f}, γ={r[8]:.3f}, meta={r[9]}) | {preview}")

            for row in rows:
                hits.append(dict(
                    id=row[0], source_id=row[1], chunk_id=row[2], content=row[3], metadata=row[4],
                    score=float(row[5]), vec=float(row[6]), beta=float(row[7]), gamma=float(row[8]), meta=int(row[9]),
                    step=sidx
                ))

            # (변경) 조기 종료 기준을 전체 hits 누적 기준으로 완화
            if len(hits) >= k:
                break

    return hits[:k]

