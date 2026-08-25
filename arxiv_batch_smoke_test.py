# coding: utf-8
"""Fast regression checks for announcement-defined arXiv batch ingestion."""
from __future__ import annotations

from arxiv_batch import _parse_atom_entries, parse_announcement_manifest


def test_announcement_manifest() -> None:
    html = """
    <html><body>
      <h3>Showing new listings for Monday, 24 August 2026</h3>
      <h3>New submissions (showing 2 of 2 entries)</h3>
      <dl>
        <dt>[1] <a href="/abs/2608.20415">arXiv:2608.20415</a></dt>
        <dt>[2] <a href="/abs/2608.20436">arXiv:2608.20436</a></dt>
      </dl>
      <h3>Cross-lists (showing 1 of 1 entries)</h3>
      <dl>
        <dt>[3] <a href="/abs/2608.19999v2">arXiv:2608.19999</a></dt>
      </dl>
      <h3>Replacements (showing 1 of 1 entries)</h3>
      <dl>
        <dt>[4] <a href="/abs/2608.10000v3">arXiv:2608.10000</a></dt>
      </dl>
    </body></html>
    """
    batch_date, ids, counts = parse_announcement_manifest(html)
    assert batch_date == "2026-08-24", batch_date
    assert ids == ["2608.20415", "2608.20436", "2608.19999"], ids
    assert counts == {"new": 2, "cross": 1, "total": 3}, counts
    assert "2608.10000" not in ids


def test_atom_metadata_parser() -> None:
    xml = b"""<?xml version='1.0' encoding='UTF-8'?>
    <feed xmlns='http://www.w3.org/2005/Atom' xmlns:arxiv='http://arxiv.org/schemas/atom'>
      <entry>
        <id>https://arxiv.org/abs/2608.20415v1</id>
        <title>  Cold neutral gas in a Galactic halo cloud  </title>
        <summary>We study H I structure and turbulence.</summary>
        <author><name>Example Author</name></author>
        <category term='astro-ph.GA'/>
        <arxiv:primary_category term='astro-ph.GA'/>
      </entry>
      <entry>
        <id>https://arxiv.org/abs/2608.19999v2</id>
        <title>Cross-listed interstellar gas study</title>
        <summary>A cross-listed paper with an external primary category.</summary>
        <author><name>Cross List</name></author>
        <category term='physics.flu-dyn'/>
        <category term='astro-ph.GA'/>
        <arxiv:primary_category term='physics.flu-dyn'/>
      </entry>
    </feed>
    """
    parsed = _parse_atom_entries(xml)
    assert set(parsed) == {"2608.20415", "2608.19999"}, parsed
    assert parsed["2608.20415"]["title"] == "Cold neutral gas in a Galactic halo cloud"
    assert parsed["2608.20415"]["authors"] == ["Example Author"]
    assert parsed["2608.19999"]["primary_category"] == "physics.flu-dyn"
    assert parsed["2608.19999"]["categories"] == ["physics.flu-dyn", "astro-ph.GA"]


def main() -> None:
    test_announcement_manifest()
    test_atom_metadata_parser()
    print("[OK] arXiv announcement-batch ingestion smoke test passed")


if __name__ == "__main__":
    main()
