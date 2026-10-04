from app.modules.rag.retrieval_service import _lexical_overlap_score, _tokenize


def test_tokenize_lowercases_and_splits():
    assert _tokenize("Visa Requirements for Japan!") == {"visa", "requirements", "for", "japan"}


def test_tokenize_empty_string():
    assert _tokenize("") == set()


def test_lexical_overlap_full_match():
    query_tokens = _tokenize("visa requirements japan")
    score = _lexical_overlap_score(query_tokens, "Visa requirements for Japan are strict.")
    assert score == 1.0


def test_lexical_overlap_partial_match():
    query_tokens = _tokenize("visa requirements japan")
    score = _lexical_overlap_score(query_tokens, "Japan is a great destination.")
    assert 0 < score < 1.0


def test_lexical_overlap_no_match():
    query_tokens = _tokenize("visa requirements japan")
    score = _lexical_overlap_score(query_tokens, "Best beaches in Bali.")
    assert score == 0.0


def test_lexical_overlap_empty_query_tokens():
    assert _lexical_overlap_score(set(), "any chunk text here") == 0.0


def test_lexical_overlap_empty_chunk():
    query_tokens = _tokenize("visa requirements")
    assert _lexical_overlap_score(query_tokens, "") == 0.0


def test_lexical_overlap_denominator_is_query_length_not_union():
    """A short query fully covered by a much longer chunk should
    still score 1.0 — overlap is measured against query coverage,
    not against how much of the chunk matched back."""
    query_tokens = _tokenize("Tokyo")
    long_chunk = "Tokyo is the capital of Japan and one of the world's largest metropolitan areas."
    assert _lexical_overlap_score(query_tokens, long_chunk) == 1.0
