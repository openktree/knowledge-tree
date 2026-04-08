"""Tests for canonical URL + DOI extraction helpers.

These helpers are the entry point for the multigraph public-cache lookup
key.  Two failure modes matter:

* **False positives** (collapsing two distinct URLs to the same canonical
  form) — would cause the cache to serve wrong content for a different
  source.  Tests below cover the params we explicitly do *not* strip.
* **False negatives** (failing to recognise two equivalent URLs as the
  same) — only cost us a missed cache hit, so we lean conservative.
"""

from __future__ import annotations

from kt_providers.fetch.canonical import (
    DOI_REGEX,
    canonicalize_url,
    extract_doi,
)

# ---------------------------------------------------------------------------
# canonicalize_url
# ---------------------------------------------------------------------------


def test_canonicalize_url_lowercases_scheme_and_host():
    assert canonicalize_url("HTTPS://Example.COM/Article") == "https://example.com/Article"


def test_canonicalize_url_drops_fragment():
    assert canonicalize_url("https://example.com/post#section-2") == "https://example.com/post"


def test_canonicalize_url_strips_default_ports():
    assert canonicalize_url("http://example.com:80/x") == "http://example.com/x"
    assert canonicalize_url("https://example.com:443/x") == "https://example.com/x"


def test_canonicalize_url_keeps_nondefault_ports():
    assert canonicalize_url("https://example.com:8443/x") == "https://example.com:8443/x"


def test_canonicalize_url_collapses_duplicate_slashes_in_path():
    assert canonicalize_url("https://example.com//foo///bar") == "https://example.com/foo/bar"


def test_canonicalize_url_strips_utm_and_known_trackers():
    raw = "https://example.com/post?utm_source=newsletter&utm_medium=email&gclid=abc&fbclid=def&id=42&page=2"
    # utm_*, gclid, fbclid removed; ``id`` and ``page`` preserved (they
    # are part of resource identity for many CMSes).
    assert canonicalize_url(raw) == "https://example.com/post?id=42&page=2"


def test_canonicalize_url_strips_mc_prefix_and_other_known_trackers():
    raw = "https://example.com/x?mc_cid=1&mc_eid=2&_ga=GA1.2&ref=twitter&igshid=foo&keep=this"
    assert canonicalize_url(raw) == "https://example.com/x?keep=this"


def test_canonicalize_url_does_not_strip_unknown_query_params():
    """Anything not on the explicit tracker list must be preserved — most
    legitimate query params (article ids, search terms, pagination) are
    indistinguishable from trackers structurally and dropping them would
    collapse distinct sources together."""
    raw = "https://example.com/search?q=cancer&from=2020&author=smith"
    assert canonicalize_url(raw) == raw


def test_canonicalize_url_preserves_query_param_order():
    raw = "https://example.com/x?c=3&a=1&b=2"
    assert canonicalize_url(raw) == raw


def test_canonicalize_url_handles_blank_query_values():
    """``?flag=`` is a meaningful pattern in some apps; the empty value
    must survive canonicalisation."""
    assert canonicalize_url("https://example.com/x?flag=&foo=bar") == "https://example.com/x?flag=&foo=bar"


def test_canonicalize_url_passes_through_non_url_strings():
    """File paths, bare strings, and anything without a scheme/host must
    not be mangled — the helper has a single job."""
    assert canonicalize_url("not-a-url") == "not-a-url"
    assert canonicalize_url("/local/file.txt") == "/local/file.txt"
    assert canonicalize_url("") == ""


def test_canonicalize_url_idempotent():
    raw = "HTTPS://Example.com:443/post?utm_source=x&id=1#frag"
    once = canonicalize_url(raw)
    twice = canonicalize_url(once)
    assert once == twice


def test_canonicalize_url_preserves_userinfo_case():
    """Userinfo (User:Pass@) is case-sensitive in some auth schemes — only
    the host portion of the netloc may be lowercased."""
    assert canonicalize_url("https://User:Pass@Example.com/x") == "https://User:Pass@example.com/x"


def test_canonicalize_url_preserves_userinfo_with_port():
    assert canonicalize_url("http://Admin@Example.COM:8080/x") == "http://Admin@example.com:8080/x"


# ---------------------------------------------------------------------------
# DOI_REGEX
# ---------------------------------------------------------------------------


def test_doi_regex_matches_canonical_pattern():
    m = DOI_REGEX.search("see 10.1038/nature12373 for details")
    assert m is not None
    assert m.group(1) == "10.1038/nature12373"


def test_doi_regex_is_anchored_on_word_boundary():
    """A trailing token like ``foo10.1038/x`` should not match — the
    ``\\b`` boundary in the regex enforces this."""
    assert DOI_REGEX.search("foo10.1038/x") is None


# ---------------------------------------------------------------------------
# extract_doi
# ---------------------------------------------------------------------------


def test_extract_doi_from_doi_org_path():
    assert extract_doi("https://doi.org/10.1038/nature12373") == "10.1038/nature12373"


def test_extract_doi_from_dx_doi_org_path():
    assert extract_doi("http://dx.doi.org/10.1038/nature12373") == "10.1038/nature12373"


def test_extract_doi_from_publisher_url_substring():
    """Some publisher URLs embed the DOI directly in the path."""
    uri = "https://link.springer.com/article/10.1007/s11192-019-03217-6"
    assert extract_doi(uri) == "10.1007/s11192-019-03217-6"


def test_extract_doi_prefers_html_metadata_over_url():
    """When the HTML page declared a citation_doi, trust it over a URL
    regex match — the page knows its own DOI better than a substring
    heuristic, and the heuristic can over-match on noisy URLs."""
    metadata = {"doi": "10.1038/nature99999", "title": "Test"}
    uri = "https://example.com/path/that/does/not/contain/a/doi"
    assert extract_doi(uri, metadata) == "10.1038/nature99999"


def test_extract_doi_falls_back_to_url_when_metadata_missing_doi():
    metadata = {"title": "Test", "author": "Jane"}
    assert extract_doi("https://doi.org/10.1038/x", metadata) == "10.1038/x"


def test_extract_doi_returns_none_when_no_doi_anywhere():
    assert extract_doi("https://example.com/blog/post-42") is None
    assert extract_doi("https://example.com/blog/post-42", {}) is None
    assert extract_doi("https://example.com/blog/post-42", {"title": "T"}) is None


def test_extract_doi_strips_trailing_punctuation():
    """The regex tail ``.rstrip('.)')`` handles DOIs grabbed from prose
    where the URL is followed by punctuation."""
    uri = "https://example.com/cite/10.1038/nature12373."
    assert extract_doi(uri) == "10.1038/nature12373"


def test_extract_doi_handles_empty_metadata_doi():
    """An empty string in ``html_metadata['doi']`` must not short-circuit
    the URL fallback — operators have seen publishers emit empty meta
    tags and we should treat that as "no metadata DOI"."""
    metadata = {"doi": "", "title": "T"}
    assert extract_doi("https://doi.org/10.1038/y", metadata) == "10.1038/y"


def test_extract_doi_rejects_non_doi_paths_under_doi_org():
    """The doi.org branch must validate that the path actually looks like
    a DOI — otherwise ``https://doi.org/about`` would return ``"about"``
    as if it were an identifier."""
    assert extract_doi("https://doi.org/about") is None
    assert extract_doi("https://doi.org/help/faq") is None
    assert extract_doi("https://www.doi.org/services") is None


def test_extract_doi_strips_trailing_punctuation_from_doi_org_path():
    """``https://doi.org/10.1038/x.`` (sentence-end period after the URL
    in prose) must yield a clean identifier — the doi.org branch and the
    regex branch both apply the same cleanup."""
    assert extract_doi("https://doi.org/10.1038/x.") == "10.1038/x"
    assert extract_doi("https://doi.org/10.1038/y)") == "10.1038/y"
