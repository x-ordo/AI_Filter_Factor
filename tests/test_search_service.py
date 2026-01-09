# tests/test_search_service.py
import pytest
from unittest.mock import MagicMock, patch
from search.service import hybrid_search, table_for_category

@pytest.fixture
def mock_db():
    with patch("search.service.get_conn") as mock_conn:
        mock_cursor = MagicMock()
        mock_conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        
        # Mock fetchall return value
        # id, source_id, chunk_id, content, metadata, score, vec, beta, gamma, meta
        mock_cursor.fetchall.return_value = [
            (1, "src1", 0, "content", {"raw_material": "test"}, 0.9, 0.8, 0.1, 0.1, 1),
            (2, "src2", 0, "content2", {"raw_material": "test2"}, 0.8, 0.7, 0.1, 0.1, 0)
        ]
        
        yield mock_cursor

@pytest.fixture
def mock_embed():
    with patch("search.service.embed_text") as mock:
        mock.return_value = [0.1] * 768  # Mock embedding vector
        yield mock

def test_hybrid_search_basic(mock_db, mock_embed):
    """기본 검색 동작 테스트"""
    results = hybrid_search(
        category="functional_food",
        query_text="테스트 질문",
        must_ingredients=["비타민"],
        k=5
    )
    
    assert len(results) > 0
    assert results[0]["source_id"] == "src1"
    assert results[0]["score"] == 0.9
    
    # Query execution check
    assert mock_db.execute.called

def test_hybrid_search_invalid_category(mock_embed):
    """잘못된 카테고리 예외처리 테스트"""
    with pytest.raises(ValueError):
        hybrid_search(
            category="invalid_category",
            query_text="test"
        )
