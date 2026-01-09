from typing import List, Dict, Any, Tuple
from psycopg2 import sql as psql

class QueryBuilder:
    @staticmethod
    def build_ingredient_filter(must_ingredients: List[str]) -> psql.SQL:
        if not must_ingredients:
            return psql.SQL("")
        
        return psql.SQL("""
          AND (
            (
              metadata ? 'raw_material'
              AND lower(metadata->>'raw_material') ILIKE ANY (%(alias_patterns_ing)s)
            )
            OR (
              jsonb_typeof(COALESCE(metadata->'raw_materials_list','[]'::jsonb)) = 'array'
              AND EXISTS (
                SELECT 1
                FROM jsonb_array_elements_text(COALESCE(metadata->'raw_materials_list','[]'::jsonb)) AS v(txt)
                WHERE lower(v.txt) ILIKE ANY (%(alias_patterns_ing)s)
              )
            )
            OR (
              metadata ? 'raw_material_kr'
              AND lower(metadata->>'raw_material_kr') ILIKE ANY (%(alias_patterns_ing)s)
            )
            OR (
              jsonb_typeof(COALESCE(metadata->'ingredients_list','[]'::jsonb)) = 'array'
              AND EXISTS (
                SELECT 1
                FROM jsonb_array_elements_text(COALESCE(metadata->'ingredients_list','[]'::jsonb)) AS v3(txt)
                WHERE lower(v3.txt) ILIKE ANY (%(alias_patterns_ing)s)
              )
            )
            OR (
              metadata ? 'ingredient'
              AND lower(metadata->>'ingredient') ILIKE ANY (%(alias_patterns_ing)s)
            )
            OR (
              metadata ? 'ingredients'
              AND lower(metadata->>'ingredients') ILIKE ANY (%(alias_patterns_ing)s)
            )
          )
        """)

    @staticmethod
    def build_efficacy_filter(use_soft_eff: bool, soft_efficacies: List[str]) -> psql.SQL:
        if not (use_soft_eff and soft_efficacies):
            return psql.SQL("")
            
        return psql.SQL("""
          AND (
            -- 1) 단일 문자열 키(functionalities / functionality / funtionalities(오타))
            COALESCE(
              to_tsvector(
                'simple',
                COALESCE(
                  metadata->>'functionalities',
                  metadata->>'functionality',
                  metadata->>'funtionalities',
                  ''
                )
              ),
              to_tsvector('simple','')
            ) @@ websearch_to_tsquery('simple', %(eff_q)s)

            OR

            -- 2) 배열 키(functionalities_list): 요소들을 문자열로 합쳐서 매칭
            (
              jsonb_typeof(COALESCE(metadata->'functionalities_list','null'::jsonb)) = 'array'
              AND to_tsvector(
                    'simple',
                    COALESCE((
                      SELECT string_agg(lower(trim(x)), ' ')
                      FROM jsonb_array_elements_text(metadata->'functionalities_list') AS x
                    ), '')
                  ) @@ websearch_to_tsquery('simple', %(eff_q)s)
            )

            OR

            -- 3) 본문(content_tsv)에도 효능 질의가 나오면 통과
            content_tsv @@ websearch_to_tsquery('simple', %(eff_q)s)

            OR

            -- 4) (보조) 라벨/클레임 유사 키
            to_tsvector(
              'simple',
              COALESCE(
                metadata->>'claims',
                metadata->>'label_claims',
                ''
              )
            ) @@ websearch_to_tsquery('simple', %(eff_q)s)
          )
        """)

    @staticmethod
    def build_hybrid_query(schema: psql.Identifier, table: psql.Identifier, 
                           ing_filter: psql.SQL, eff_filter: psql.SQL) -> psql.Composed:
        return psql.SQL("""
          WITH q AS (
            SELECT %(qvec)s::vector AS qvec,
                   %(qnorm)s::text  AS qnorm,
                   %(qcoll)s::text  AS qcoll
          ), base AS (
            SELECT id, source_id, chunk_id, content, metadata,

                   -- α: 벡터 유사도
                   1 - (embedding <=> (SELECT qvec FROM q)) AS vec_score,

                   -- β: content trigram
                   similarity(lower(content), (SELECT qnorm FROM q)) AS beta_score,

                   -- γ: 공백 제거 비교까지의 trigram 보조
                   GREATEST(
                     similarity(lower(content), (SELECT qnorm FROM q)),
                     similarity(regexp_replace(lower(content), '\s','', 'g'), (SELECT qcoll FROM q))
                   ) AS gamma_score,

                   -- δ: 메타 부스트 (ingredient/brand/product 매치)
                   (
                     -- ingredient: raw_material / raw_materials_list (+확장 키)
                     (CASE WHEN metadata ? 'raw_material'
                           AND lower(metadata->>'raw_material') ILIKE ANY (%(alias_patterns_ing)s)
                           THEN 1 ELSE 0 END)
                     +
                     (CASE WHEN jsonb_typeof(COALESCE(metadata->'raw_materials_list','[]'::jsonb)) = 'array'
                           AND EXISTS (
                             SELECT 1
                             FROM jsonb_array_elements_text(COALESCE(metadata->'raw_materials_list','[]'::jsonb)) AS v(txt)
                             WHERE lower(v.txt) ILIKE ANY (%(alias_patterns_ing)s)
                           )
                           THEN 1 ELSE 0 END)
                     +
                     (CASE WHEN metadata ? 'raw_material_kr'
                           AND lower(metadata->>'raw_material_kr') ILIKE ANY (%(alias_patterns_ing)s)
                           THEN 1 ELSE 0 END)
                     +
                     (CASE WHEN jsonb_typeof(COALESCE(metadata->'ingredients_list','[]'::jsonb)) = 'array'
                           AND EXISTS (
                             SELECT 1
                             FROM jsonb_array_elements_text(COALESCE(metadata->'ingredients_list','[]'::jsonb)) AS v3(txt)
                             WHERE lower(v3.txt) ILIKE ANY (%(alias_patterns_ing)s)
                           )
                           THEN 1 ELSE 0 END)
                     +
                     (CASE WHEN metadata ? 'ingredient'
                           AND lower(metadata->>'ingredient') ILIKE ANY (%(alias_patterns_ing)s)
                           THEN 1 ELSE 0 END)
                     +
                     (CASE WHEN metadata ? 'ingredients'
                           AND lower(metadata->>'ingredients') ILIKE ANY (%(alias_patterns_ing)s)
                           THEN 1 ELSE 0 END)
                     +
                     -- brand/company
                     (CASE WHEN lower(COALESCE(metadata->>'ltd','')) ILIKE ANY (%(alias_patterns_brand)s) THEN 1 ELSE 0 END)
                     +
                     (CASE WHEN jsonb_typeof(COALESCE(metadata->'company_list','[]'::jsonb)) = 'array'
                           AND EXISTS (
                             SELECT 1
                             FROM jsonb_array_elements_text(COALESCE(metadata->'company_list','[]'::jsonb)) AS v2(txt)
                             WHERE lower(v2.txt) ILIKE ANY (%(alias_patterns_brand)s)
                           )
                           THEN 1 ELSE 0 END)
                     +
                     (CASE WHEN lower(COALESCE(metadata->>'brand','')) ILIKE ANY (%(alias_patterns_brand)s) THEN 1 ELSE 0 END)
                     +
                     -- product
                     (CASE WHEN lower(COALESCE(metadata->>'product','')) ILIKE ANY (%(alias_patterns_prod)s) THEN 1 ELSE 0 END)
                     +
                     (CASE WHEN lower(COALESCE(metadata->>'product_name','')) ILIKE ANY (%(alias_patterns_prod)s) THEN 1 ELSE 0 END)
                   )::int AS meta_score

            FROM {schema}.{table}
            WHERE true
            {ing_filter}
            {eff_filter}
          )
          SELECT id, source_id, chunk_id, content, metadata,
                 %(alpha)s*vec_score + %(beta)s*beta_score + %(gamma)s*gamma_score + %(delta)s*(meta_score) AS score,
                 vec_score, beta_score, gamma_score, meta_score
          FROM base
          ORDER BY score DESC
          LIMIT %(k)s
        """).format(
            schema=schema,
            table=table,
            ing_filter=ing_filter,
            eff_filter=eff_filter,
        )
