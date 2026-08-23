# coding: utf-8
"""Regression checks for AstroBrief ingestion and semantic scope decisions."""
from __future__ import annotations

import datetime as dt

import semantic_daily
from semantic_daily import _fetch_arxiv_day, apply_final_scope_guard
from semantic_recommender import score_papers


def paper(pid, title, abstract, primary):
    return {
        "id": pid,
        "title": title,
        "abstract": abstract,
        "authors": ["Test Author"],
        "subjects": primary,
        "categories": [primary],
        "primary_category": primary,
        "main_page": f"https://arxiv.org/abs/{pid}",
        "pdf": f"https://arxiv.org/pdf/{pid}.pdf",
    }


def test_atom_ingestion() -> None:
    """Verify structured Atom parsing, version stripping, and astro-ph filtering."""
    xml = b"""<?xml version='1.0' encoding='UTF-8'?>
    <feed xmlns='http://www.w3.org/2005/Atom' xmlns:arxiv='http://arxiv.org/schemas/atom'>
      <entry>
        <id>https://arxiv.org/abs/2608.12345v2</id>
        <title>  Dense molecular gas in a nearby cloud  </title>
        <summary>We study cold molecular gas and star formation.</summary>
        <author><name>Example Author</name></author>
        <category term='astro-ph.GA'/>
        <category term='astro-ph.SR'/>
        <arxiv:primary_category term='astro-ph.GA'/>
      </entry>
      <entry>
        <id>https://arxiv.org/abs/2608.99999v1</id>
        <title>Non-astronomy control</title>
        <summary>A control entry outside astronomy.</summary>
        <author><name>Control Author</name></author>
        <category term='physics.plasm-ph'/>
        <arxiv:primary_category term='physics.plasm-ph'/>
      </entry>
    </feed>
    """

    class FakeResponse:
        content = xml

        @staticmethod
        def raise_for_status():
            return None

    calls = []
    original_get = semantic_daily.requests.get

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse()

    semantic_daily.requests.get = fake_get
    try:
        papers = _fetch_arxiv_day(dt.date(2026, 8, 21))
    finally:
        semantic_daily.requests.get = original_get

    assert len(papers) == 1, papers
    parsed = papers[0]
    assert parsed["id"] == "2608.12345", parsed
    assert parsed["title"] == "Dense molecular gas in a nearby cloud", parsed
    assert parsed["authors"] == ["Example Author"], parsed
    assert parsed["categories"] == ["astro-ph.GA", "astro-ph.SR"], parsed
    assert parsed["primary_category"] == "astro-ph.GA", parsed
    assert calls and calls[0][0] == semantic_daily.ARXIV_API, calls
    assert "cat:astro-ph.*" in calls[0][1]["params"]["search_query"], calls
    assert "submittedDate:[202608210000 TO 202608212359]" in calls[0][1]["params"]["search_query"], calls
    assert calls[0][1]["headers"]["User-Agent"].startswith("AstroBrief/"), calls


def test_semantic_scope() -> None:
    papers = [
        paper(
            "test.0001",
            "Dense molecular cloud kinematics and chemistry in an infrared dark cloud",
            "We use molecular-line observations of NH3, HCO+ and C18O to study dense clumps, infall, turbulence, and early massive star formation in an IRDC.",
            "astro-ph.GA",
        ),
        paper(
            "test.0002",
            "High Velocity Neutral Gas in the Fermi Bubbles",
            "We use H I data to study neutral clouds entrained in the Milky Way nuclear wind and their kinematics above the Galactic Centre.",
            "astro-ph.GA",
        ),
        paper(
            "test.0003",
            "Operational Solar Flare Peak Flux Nowcasting",
            "We predict solar flare peak X-ray flux from Solar Orbiter and GOES observations using machine learning for space weather.",
            "astro-ph.SR",
        ),
        paper(
            "test.0004",
            "Outflows in steep density gradients in tidal disruption events",
            "We model shocks and turbulent outflows from tidal disruption events and luminous fast blue optical transients around compact objects.",
            "astro-ph.HE",
        ),
        paper(
            "test.0005",
            "A model for the enhanced production rate of early-type hypervelocity stars in the Galactic halo",
            "The stars were ejected from the Galactic center by a black-hole gravitational slingshot. We constrain their stellar formation history and orbital dynamics in the nuclear star cluster.",
            "astro-ph.GA",
        ),
        paper(
            "test.0006",
            "Multiphase gas in the circumgalactic medium of nearby galaxies",
            "We combine ultraviolet absorption and H I observations to measure cool and warm CGM gas, cloud kinematics, ionization, gas accretion, and the baryon cycle around low-redshift galaxies.",
            "astro-ph.GA",
        ),
        paper(
            "test.0007",
            "Intergalactic hydrogen during cosmic reionization",
            "We model the ionization state of the intergalactic medium and the cosmic web at redshift seven using cosmological simulations and 21-cm statistics.",
            "astro-ph.CO",
        ),
    ]

    scored, summary = score_papers(papers)
    scored, summary = apply_final_scope_guard(scored, summary)
    by_id = {p["id"]: p for p in scored}
    for p in scored:
        print(
            p["id"],
            p["priority"],
            p["score"],
            p["best_positive_topic"],
            p["scope_reason"],
        )

    assert by_id["test.0001"]["priority"] in {"A", "B"}, by_id["test.0001"]
    assert by_id["test.0002"]["priority"] in {"A", "B"}, by_id["test.0002"]
    assert by_id["test.0003"]["priority"] in {"C", "SKIP"}, by_id["test.0003"]
    assert by_id["test.0004"]["priority"] in {"C", "SKIP"}, by_id["test.0004"]
    assert by_id["test.0005"]["priority"] in {"C", "SKIP"}, by_id["test.0005"]
    assert by_id["test.0006"]["priority"] in {"A", "B"}, by_id["test.0006"]
    assert by_id["test.0007"]["priority"] in {"C", "SKIP"}, by_id["test.0007"]
    assert summary["candidate_count"] == 7


def main() -> None:
    test_atom_ingestion()
    test_semantic_scope()
    print("[OK] AstroBrief smoke test passed")


if __name__ == "__main__":
    main()
