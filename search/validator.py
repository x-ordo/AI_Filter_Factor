# search/validator.py
import re
from typing import List, Dict, Optional

_VERDICT_EMOJI_RE = re.compile(r"[✅⚠️❌]")
_WS_RE = re.compile(r"\s+")

def _norm(s: str) -> str:
    s = s or ""
    s = s.lower()
    s = _WS_RE.sub("", s)
    s = re.sub(r"[^\w가-힣]", "", s)
    return s

def _claim_terms_from_question(q: str, parsed_soft: List[str]) -> List[str]:
    qn = _norm(q)
    terms = set(t for t in parsed_soft if t)

    # 휴리스틱 키워드
    if "눈" in q:
        terms.update(["눈 건강","시력","황반색소밀도 유지"])
    if "골다공증" in q:
        terms.update(["골다공증","골다공증 위험 감소","뼈 건강"])
    if "피로" in q:
        terms.add("피로개선")
    if "혈압" in q:
        terms.update(["혈압","혈압 개선","혈압 관리"])
    if "혈행" in q or "혈액흐름" in qn:
        terms.update(["혈행 개선","혈액 흐름 개선"])

    # 정규화 버전도 같이 보관
    out = set()
    for t in terms:
        if not t: 
            continue
        out.add(t)
        out.add(_norm(t))
    return list(out)

def _functions_from_evidence(evidence: List[dict]) -> List[str]:
    funs = []
    for ev in evidence or []:
        m = ev.get("meta") or {}
        # meta.functions (리스트/문자열 모두 대응)
        fs = m.get("functions") or []
        if isinstance(fs, str):
            fs = [fs]
        for f in fs:
            if f:
                funs.append(str(f))
        # 텍스트에서 흔한 패턴 보강
        txt = (ev.get("text") or ev.get("content") or "").strip()
        for pat in ["눈 건강","시력","황반색소","골다공증","뼈","혈압","혈행","피로개선",
                    "기억력 개선","항산화","혈중 중성지질","면역력 증진"]:
            if pat in txt:
                funs.append(pat)
    # 정규화된 버전도 추가
    out = set()
    for f in funs:
        out.add(f)
        out.add(_norm(f))
    return list(out)

def extract_verdict_symbol(answer_text: str) -> str:
    m_section = re.search(r"(?s)###\s*판단(.*?)(###|$)", answer_text)
    if m_section:
        m_icon = _VERDICT_EMOJI_RE.search(m_section.group(1))
        if m_icon:
            return m_icon.group(0)
    m_any = _VERDICT_EMOJI_RE.search(answer_text)
    if m_any:
        return m_any.group(0)
    return "⚠️"

def client_safe_text(answer_text: str) -> str:
    # 내부 표현 정리: "데이터 한계" → "근거 현황"
    answer_text = answer_text.replace("데이터 한계", "근거 현황")
    # 시스템/오류류 문구 정제
    answer_text = answer_text.replace("시스템 오류", "내부 처리 중단")
    # 안내 문구 보강(표준 문장)
    answer_text = answer_text.replace(
        "현재 DB 기준, 식약처 저장 정보 없음",
        "현 시점 사내 DB(식약처 고시·개별인정 자료 정규화)에 등록된 직접 근거를 확인하지 못했습니다"
    )
    return answer_text

def verdict_guard(question_text: str, payload: dict, llm_verdict: str) -> str:
    """
    BPMF만 있을 때도 질문 주장과 기능이 '직접·동의어 수준'으로 연결되면 ✅,
    연결이 없으면 ⚠️ 로 보정.
    """
    ev = payload.get("evidence", []) or []
    # 근거 유형 집계
    mf_cnt = sum(1 for e in ev if (e.get("kind") or "").upper() == "MF")
    bpmf_cnt = sum(1 for e in ev if (e.get("kind") or "").upper() == "BPMF")

    # 질문 주장 후보
    parsed = payload.get("parsed") or {}
    claim_terms = _claim_terms_from_question(question_text, parsed.get("functions", []))
    claim_norm = set(_norm(t) for t in claim_terms)

    # 근거 기능 후보
    funs = _functions_from_evidence(ev)
    fun_norm = set(_norm(f) for f in funs)

    def has_direct_match() -> bool:
        if not claim_norm or not fun_norm:
            return False
        # 부분 포함 허용
        for c in claim_norm:
            for f in fun_norm:
                if c and f and (c in f or f in c):
                    return True
        return False

    # 1) 반증 로직은 모델/프롬프트에 맡기고 여기선 미적용(데이터 부족)
    # 2) 매칭 보정
    if bpmf_cnt > 0 and has_direct_match():
        return "✅"
    if bpmf_cnt > 0 and not has_direct_match():
        return "⚠️"
    # MF만 있는데도 매치가 없으면 ⚠️
    if mf_cnt > 0 and not has_direct_match():
        return "⚠️"
    # 둘 다 없으면 ⚠️
    if mf_cnt == 0 and bpmf_cnt == 0:
        return "⚠️"
    # 기본값: 모델 판정 유지
    return llm_verdict or "⚠️"
