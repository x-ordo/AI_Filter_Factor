# AI Filter Factor

기능성 식품 및 화장품 데이터를 기반으로 사용자의 질문에 대해 **"근거 기반의 팩트 체크(Fact-Checking)"** 답변을 제공하는 **RAG(Retrieval-Augmented Generation)** 시스템입니다.

## 📂 프로젝트 구조

```bash
/AI_Filter_Factor
├── ingest/                 # [데이터 파이프라인]
│   ├── run_ingest.py       # 데이터 적재 메인 스크립트 (ETL)
│   ├── load_views.py       # 원본 데이터 로드
│   ├── normalize.py        # 텍스트 정규화
│   ├── text_chunker.py     # 텍스트 청킹 (Chunking)
│   ├── embed.py            # 임베딩 생성 (Ollama)
│   └── upsert.py           # DB 저장 (PostgreSQL/pgvector)
│
├── search/                 # [검색 및 서비스 로직]
│   ├── api_http.py         # FastAPI 엔드포인트 (/ask)
│   ├── service.py          # 검색 오케스트레이션 (Hybrid Search)
│   ├── query_builder.py    # SQL 쿼리 빌더 (Query Builder 패턴 적용)
│   ├── controller.py       # RAG 흐름 제어 (Flow Control)
│   ├── validator.py        # 답변 검증 및 보정 (Verdict Guard)
│   ├── context_builder.py  # LLM 컨텍스트 조립
│   ├── prompt_template.py  # 프롬프트 템플릿 관리
│   ├── llm_client.py       # LLM API 클라이언트
│   └── config.py           # 환경 설정 및 상수 관리
│
├── tests/                  # [테스트]
│   ├── test_search_service.py # 검색 서비스 단위 테스트
│   └── ...
│
├── requirements.txt        # 의존성 패키지 목록
└── .env                    # 환경 변수 설정 (DB, Ollama 등)
```

## 🚀 주요 기능

### 1. Hybrid Search (하이브리드 검색)
*   **Vector Search**: `nomic-embed-text` 모델을 이용한 의미론적 검색.
*   **Keyword Search**: PostgreSQL `pg_trgm`을 이용한 키워드 유사도 검색.
*   **Metadata Boosting**: 브랜드, 제품명, 성분 일치 시 가중치 부여.
*   **Backoff Strategy**: 검색 결과 부족 시 조건을 단계적으로 완화하여 재검색.

### 2. Verdict Guard (팩트 체크 보정)
*   LLM이 생성한 답변을 시스템이 2차 검증.
*   근거 데이터(Evidence) 내에 사용자 질문의 의도에 부합하는 기능성 문구가 없을 경우, 답변의 신뢰도 등급을 강제로 하향 조정(⚠️)하여 환각(Hallucination) 방지.

## 🛠 기술 스택
*   **Backend**: Python, FastAPI
*   **Database**: PostgreSQL (pgvector, pg_trgm)
*   **AI/ML**: Ollama (Embedding & LLM)
*   **Testing**: pytest

## 🧪 테스트 실행
```bash
# 의존성 설치
pip install -r requirements.txt

# 테스트 실행
export PYTHONPATH=$PYTHONPATH:.
pytest tests/
```