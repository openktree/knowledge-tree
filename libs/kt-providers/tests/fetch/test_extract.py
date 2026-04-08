"""Unit tests for the shared extract helpers."""

from kt_providers.fetch.extract import (
    classify_content_type,
    extract_html,
    extract_html_metadata,
    extract_image,
    extract_text,
)


def test_classify_content_type_pdf():
    assert classify_content_type("application/pdf") == "pdf"


def test_classify_content_type_image():
    assert classify_content_type("image/png") == "image"


def test_classify_content_type_html():
    assert classify_content_type("text/html; charset=utf-8") == "text"


def test_classify_content_type_json():
    assert classify_content_type("application/json") == "text"


def test_classify_content_type_unknown():
    assert classify_content_type("application/zip") == "unknown"


def test_extract_html_short_returns_error():
    r = extract_html("https://x.com", "<html><body></body></html>", "text/html")
    assert r.success is False
    assert "insufficient" in (r.error or "").lower()


def test_extract_html_with_real_content():
    html = (
        "<html><body><article><p>"
        + "This is a real article with enough body text to clear the minimum "
        + "extraction threshold for trafilatura. " * 3
        + "</p></article></body></html>"
    )
    r = extract_html("https://x.com/article", html, "text/html")
    assert r.success is True
    assert r.content is not None
    assert "real article" in r.content


def test_extract_text_too_short():
    r = extract_text("https://x.com", "hi", "text/plain")
    assert r.success is False


def test_extract_text_long_enough():
    r = extract_text("https://x.com", "x" * 200, "text/plain")
    assert r.success is True


def test_extract_image_empty_bytes():
    r = extract_image("https://x.com/img.png", b"", "image/png")
    assert r.success is False
    assert "empty" in (r.error or "").lower()


def test_extract_image_returns_raw_bytes():
    r = extract_image("https://x.com/img.png", b"\x89PNG fake", "image/png")
    assert r.is_image is True
    assert r.raw_bytes == b"\x89PNG fake"


# ---------------------------------------------------------------------------
# extract_html_metadata + citation_doi scraping
# ---------------------------------------------------------------------------


def test_extract_html_metadata_pulls_citation_doi():
    """Most academic publishers expose the DOI via a citation_doi meta
    tag. Capturing it here means every fetcher (not just the dedicated
    DOI provider) surfaces a DOI for the multigraph public-cache lookup."""
    html = (
        "<html><head>"
        '<meta name="citation_doi" content="10.1038/nature12373">'
        "<title>Test</title>"
        "</head><body><article><p>" + "x" * 200 + "</p></article></body></html>"
    )
    meta = extract_html_metadata(html)
    assert meta is not None
    assert meta.get("doi") == "10.1038/nature12373"


def test_extract_html_metadata_handles_reversed_attribute_order():
    """Some publishers emit ``content="..." name="citation_doi"`` instead
    of ``name="citation_doi" content="..."``. The regex must tolerate both."""
    html = '<html><head><meta content="10.1038/x" name="citation_doi" /></head><body>' + "x" * 200 + "</body></html>"
    meta = extract_html_metadata(html)
    assert meta is not None
    assert meta.get("doi") == "10.1038/x"


def test_extract_html_metadata_handles_single_quoted_attrs():
    html = "<html><head><meta name='citation_doi' content='10.1038/y' /></head><body>" + "x" * 200 + "</body></html>"
    meta = extract_html_metadata(html)
    assert meta is not None
    assert meta.get("doi") == "10.1038/y"


def test_extract_html_metadata_no_doi_when_meta_absent():
    html = "<html><head><title>Plain</title></head><body><article><p>" + "x" * 200 + "</p></article></body></html>"
    meta = extract_html_metadata(html)
    # ``doi`` key may simply be absent — we just want it to NOT be present
    # or to be falsy.  Other trafilatura keys may or may not be set.
    if meta is not None:
        assert not meta.get("doi")
