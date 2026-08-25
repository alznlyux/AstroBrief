# coding: utf-8
"""Fast regression checks for listing-defined arXiv announcement ingestion."""
from __future__ import annotations

from arxiv_batch import parse_announcement_manifest, parse_announcement_page


def _item(paper_id: str, title: str, authors: str, subjects: str, abstract: str, version: str = "") -> str:
    href_id = paper_id + version
    author_links = "".join(
        f'<a href="/search/astro-ph?searchtype=author&query={i}">{name.strip()}</a>'
        for i, name in enumerate(authors.split(","), start=1)
    )
    return f"""
      <dt><a href="/abs/{href_id}" title="Abstract">arXiv:{href_id}</a></dt>
      <dd><div class="meta">
        <div class="list-title mathjax"><span class="descriptor">Title:</span> {title}</div>
        <div class="list-authors"><span class="descriptor">Authors:</span> {author_links}</div>
        <div class="list-subjects"><span class="descriptor">Subjects:</span> {subjects}</div>
        <p class="mathjax">{abstract}</p>
      </div></dd>
    """


def test_complete_announcement_page() -> None:
    # Mirror the real arXiv page structure: each section has its own <dl>.
    html = f"""
    <html><body><div id="content">
      <ul>
        <li><a href="#item0">New submissions</a></li>
        <li><a href="#item3">Cross-lists</a></li>
        <li><a href="#item4">Replacements</a></li>
      </ul>
      <h3>Showing new listings for Monday, 24 August 2026</h3>
      <div class="paging">Total of 4 entries</div>
      <h3>New submissions</h3>
      <dl>
        {_item('2608.20415', 'Cold neutral gas in a Galactic halo cloud', 'Example Author, Second Author', 'Astrophysics of Galaxies (astro-ph.GA); Solar and Stellar Astrophysics (astro-ph.SR)', 'We study H I structure and turbulence.')}
        {_item('2608.20436', 'Dense cores and magnetic fields', 'Core Author', 'Solar and Stellar Astrophysics (astro-ph.SR)', 'We study prestellar dense cores.')}
      </dl>
      <h3>Cross-lists</h3>
      <dl>
        {_item('2608.19999', 'Cross-listed interstellar gas study', 'Cross List', 'Fluid Dynamics (physics.flu-dyn); Astrophysics of Galaxies (astro-ph.GA)', 'A cross-listed paper with interstellar gas.', version='v2')}
      </dl>
      <h3>Replacements</h3>
      <dl>
        {_item('2608.10000', 'Replacement paper', 'Old Author', 'Astrophysics of Galaxies (astro-ph.GA)', 'This replacement must not enter screening.', version='v3')}
      </dl>
    </div></body></html>
    """

    batch_date, papers, counts = parse_announcement_page(html)
    assert batch_date == "2026-08-24", batch_date
    assert counts == {"new": 2, "cross": 1, "total": 3}, counts
    assert [p["id"] for p in papers] == ["2608.20415", "2608.20436", "2608.19999"]
    assert papers[0]["authors"] == ["Example Author", "Second Author"]
    assert papers[0]["categories"] == ["astro-ph.GA", "astro-ph.SR"]
    assert papers[0]["primary_category"] == "astro-ph.GA"
    assert papers[2]["categories"] == ["physics.flu-dyn", "astro-ph.GA"]
    assert papers[2]["primary_category"] == "physics.flu-dyn"
    assert "2608.10000" not in {p["id"] for p in papers}

    manifest_date, ids, manifest_counts = parse_announcement_manifest(html)
    assert manifest_date == batch_date
    assert ids == [p["id"] for p in papers]
    assert manifest_counts == counts


def test_page_without_replacements() -> None:
    html = f"""
    <html><body><div id="content">
      <ul>
        <li><a href="#item0">New submissions</a></li>
        <li><a href="#item2">Cross-lists</a></li>
      </ul>
      <h3>Showing new listings for Tuesday, 25 August 2026</h3>
      <div class="paging">Total of 2 entries</div>
      <h3>New submissions</h3>
      <dl>
        {_item('2608.21000', 'New molecular cloud paper', 'New Author', 'Solar and Stellar Astrophysics (astro-ph.SR)', 'Molecular cloud abstract.')}
      </dl>
      <h3>Cross-lists</h3>
      <dl>
        {_item('2608.20000', 'Cross-listed turbulence paper', 'Fluid Author', 'Fluid Dynamics (physics.flu-dyn); Astrophysics of Galaxies (astro-ph.GA)', 'Interstellar turbulence abstract.')}
      </dl>
    </div></body></html>
    """
    batch_date, papers, counts = parse_announcement_page(html)
    assert batch_date == "2026-08-25"
    assert [p["id"] for p in papers] == ["2608.21000", "2608.20000"]
    assert counts == {"new": 1, "cross": 1, "total": 2}


def test_missing_metadata_fails_closed() -> None:
    html = """
    <html><body><div id="content">
      <ul><li><a href="#item0">New submissions</a></li></ul>
      <h3>Showing new listings for Tuesday, 25 August 2026</h3>
      <div class="paging">Total of 1 entries</div>
      <dl>
        <dt><a href="/abs/2608.21000" title="Abstract">arXiv:2608.21000</a></dt>
        <dd><div class="meta">
          <div class="list-title mathjax">Title: Broken entry</div>
          <div class="list-authors">Authors: Missing Abstract</div>
          <div class="list-subjects">Subjects: Solar and Stellar Astrophysics (astro-ph.SR)</div>
        </div></dd>
      </dl>
    </div></body></html>
    """
    try:
        parse_announcement_page(html)
    except RuntimeError as exc:
        assert "metadata" in str(exc).lower() or "abstract" in str(exc).lower()
    else:
        raise AssertionError("Missing abstract should fail closed")


def main() -> None:
    test_complete_announcement_page()
    test_page_without_replacements()
    test_missing_metadata_fails_closed()
    print("[OK] arXiv listing ingestion smoke test passed")


if __name__ == "__main__":
    main()
