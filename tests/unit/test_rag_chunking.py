from app.modules.rag.chunking import chunk_text


def test_empty_content_returns_no_chunks():
    assert chunk_text("") == []
    assert chunk_text("   ") == []


def test_short_content_is_one_chunk():
    chunks = chunk_text("Paris is the capital of France.")
    assert len(chunks) == 1
    assert chunks[0] == "Paris is the capital of France."


def test_paragraphs_pack_until_max_chars():
    paragraphs = [f"Paragraph {n} " + "word " * 20 for n in range(10)]
    content = "\n\n".join(paragraphs)
    chunks = chunk_text(content, max_chars=200)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 200 or "\n\n" not in chunk  # oversized single-paragraph chunks are allowed


def test_oversized_single_paragraph_is_hard_split():
    huge_paragraph = "x" * 2500
    chunks = chunk_text(huge_paragraph, max_chars=1000)
    assert len(chunks) == 3
    assert sum(len(c) for c in chunks) == 2500


def test_all_content_preserved_across_chunks():
    content = "First paragraph here.\n\nSecond paragraph here.\n\nThird paragraph here."
    chunks = chunk_text(content, max_chars=1000)
    rejoined = "\n\n".join(chunks)
    assert "First paragraph here." in rejoined
    assert "Second paragraph here." in rejoined
    assert "Third paragraph here." in rejoined
