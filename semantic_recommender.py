# coding: utf-8
"""Semantic recommendation engine for AstroBrief.

Pipeline:
  SPECTER2 scientific embeddings
  -> positive-vs-negative semantic contrast
  -> explicit target-object evidence / category gate
  -> small local zero-shot NLI reranker
  -> stable research-scope calibration

No paid API and no local workstation are required. Models are downloaded from
Hugging Face and cached by GitHub Actions.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import numpy as np
import torch
from adapters import AutoAdapterModel
from transformers import AutoModelForSequenceClassification, AutoTokenizer

SPECTER_BASE = "allenai/specter2_base"
SPECTER_QUERY = "allenai/specter2_adhoc_query"
SPECTER_PAPER = "allenai/specter2"
NLI_MODEL = "cross-encoder/nli-deberta-v3-xsmall"

RANK = {"SKIP": 0, "C": 1, "B": 2, "A": 3}
RANK_TO_PRIORITY = {0: "SKIP", 1: "C", 2: "B", 3: "A"}
SECONDARY_ASTRO = {"astro-ph.HE", "astro-ph.IM", "astro-ph.CO", "astro-ph.EP"}

DOMAIN_PATTERNS = [
    (r"\binterstellar medium\b", 2.5),
    (r"\bISM\b", 2.0),
    (r"\bmolecular clouds?\b", 3.0),
    (r"\bmolecular gas\b", 2.5),
    (r"\bgiant molecular clouds?\b", 3.0),
    (r"\bneutral hydrogen\b", 3.0),
    (r"\bneutral clouds?\b", 2.5),
    (r"\bH\s*I\s+(?:21\s*-?\s*cm|data|emission|absorption|clouds?|gas|survey|shells?|column)\b", 3.0),
    (r"\bN[_ ]?HI\b", 2.0),
    (r"\bHISA\b", 3.0),
    (r"\bHINSA\b", 3.0),
    (r"\bCNM\b", 2.0),
    (r"\bWNM\b", 2.0),
    (r"\bcold neutral (?:medium|gas)\b", 3.0),
    (r"\batomic[- ]to[- ]molecular\b", 3.0),
    (r"\bhigh[- ]velocity clouds?\b", 3.0),
    (r"\bHVCs?\b", 3.0),
    (r"\bintermediate[- ]velocity clouds?\b", 2.5),
    (r"\bIVCs?\b", 2.5),
    (r"\bMagellanic Stream\b", 3.0),
    (r"\bGalactic halo (?:gas|medium|H\s*I|clouds?)\b", 2.5),
    (r"\b(?:gas|H\s*I|neutral|ionized|clouds?) (?:in|of) the Galactic halo\b", 2.5),
    (r"\bhalo gas\b", 2.0),
    (r"\bcircumgalactic medium\b", 3.0),
    (r"\bCGM\b", 2.5),
    (r"\bgalactic fountain\b", 2.0),
    (r"\bgas accretion\b", 1.5),
    (r"\bstar[- ]forming regions?\b", 2.5),
    (r"\bstar formation\b", 1.5),
    (r"\bprotostars?\b", 2.0),
    (r"\bprotostellar\b", 2.0),
    (r"\bprestellar\b", 2.0),
    (r"\byoung stellar objects?\b", 2.0),
    (r"\bYSOs?\b", 2.0),
    (r"\binfrared dark clouds?\b", 3.0),
    (r"\bIRDCs?\b", 3.0),
    (r"\bH\s*II regions?\b", 2.5),
    (r"\bHII regions?\b", 2.5),
    (r"\bsuperbubbles?\b", 2.0),
    (r"\bdense cores?\b", 2.0),
    (r"\bdense clumps?\b", 2.0),
    (r"\b13CO\b", 1.5),
    (r"\bC18O\b", 1.5),
    (r"\bNH3\b", 1.0),
    (r"\bHCO\+\b", 1.0),
    (r"\bcosmic[- ]ray ionization\b", 2.0),
    (r"\b3D dust\b", 1.5),
    (r"\bGalactic plane\b", 1.0),
    (r"\bCentral Molecular Zone\b", 3.0),
    (r"\bCMZ\b", 2.5),
    (r"\bGalactic Cent(?:re|er)\b", 2.0),
    (r"\bFermi Bubbles?\b", 1.5),
    (r"\binterstellar magnetic\b", 2.5),
    (r"\bmolecular[- ]line (?:survey|observations?|emission)\b", 2.0),
]

DIRECT_TITLE_PATTERNS = [
    r"\bmolecular clouds?\b", r"\bmolecular gas\b", r"\binterstellar\b",
    r"\bHISA\b", r"\bHINSA\b", r"\bneutral gas\b", r"\bneutral clouds?\b",
    r"\bhigh[- ]velocity clouds?\b", r"\bHVCs?\b",
    r"\bintermediate[- ]velocity clouds?\b", r"\bIVCs?\b",
    r"\bMagellanic Stream\b", r"\bhalo gas\b",
    r"\bGalactic halo\b.*\b(?:gas|H\s*I|neutral|ionized|clouds?|medium)\b",
    r"\b(?:gas|H\s*I|neutral|ionized|clouds?|medium)\b.*\bGalactic halo\b",
    r"\bcircumgalactic medium\b", r"\bCGM\b", r"\bgalactic fountain\b",
    r"\bstar formation\b", r"\bstar[- ]forming\b", r"\bprotostars?\b",
    r"\bprotostellar\b", r"\bprestellar\b", r"\bIRDCs?\b",
    r"\binfrared dark clouds?\b", r"\bH\s*II region\b", r"\bHII region\b",
    r"\bCentral Molecular Zone\b", r"\bCMZ\b", r"\bFermi Bubbles?\b",
]


def _lexical_scores(papers: list[dict], topics: dict) -> np.ndarray:
    rows = []
    for p in papers:
        text = (p["title"] + " " + p["abstract"]).lower()
        vals = []
        for topic in topics.values():
            hits = 0.0
            for term in topic.get("lexical_terms", []):
                if re.search(r"(?<!\w)" + re.escape(term.lower()) + r"(?!\w)", text):
                    hits += 1.0
            vals.append(1.0 - math.exp(-hits / 3.0))
        rows.append(vals)
    return np.asarray(rows, dtype=float)


def _embed(model, tokenizer, texts: list[str], batch_size: int = 16) -> np.ndarray:
    vecs = []
    model.eval()
    with torch.no_grad():
        for start in range(0, len(texts), batch_size):
            batch = texts[start:start + batch_size]
            inputs = tokenizer(
                batch, padding=True, truncation=True, max_length=512,
                return_tensors="pt", return_token_type_ids=False,
            )
            vecs.append(model(**inputs).last_hidden_state[:, 0, :].cpu().numpy())
    return np.concatenate(vecs, axis=0)


def _cosine(query_vectors: np.ndarray, paper_vectors: np.ndarray) -> np.ndarray:
    q = query_vectors / np.clip(np.linalg.norm(query_vectors, axis=1, keepdims=True), 1e-12, None)
    p = paper_vectors / np.clip(np.linalg.norm(paper_vectors, axis=1, keepdims=True), 1e-12, None)
    return p @ q.T


def _load_specter2():
    print("[INFO] Loading SPECTER2")
    tok = AutoTokenizer.from_pretrained(SPECTER_BASE)
    model = AutoAdapterModel.from_pretrained(SPECTER_BASE)
    model.load_adapter(SPECTER_QUERY, source="hf", load_as="adhoc_query", set_active=True)
    model.load_adapter(SPECTER_PAPER, source="hf", load_as="proximity", set_active=False)
    return model, tok


def _domain_evidence(p: dict) -> tuple[float, float, list[str]]:
    total = title_total = 0.0
    hits = []
    for pattern, weight in DOMAIN_PATTERNS:
        if re.search(pattern, p["title"], flags=re.I):
            total += 1.5 * weight
            title_total += 1.5 * weight
            hits.append("title:" + pattern)
        elif re.search(pattern, p["abstract"], flags=re.I):
            total += weight
            hits.append("abstract:" + pattern)
    return total, title_total, hits


def _direct_title(title: str) -> bool:
    return any(re.search(p, title, flags=re.I) for p in DIRECT_TITLE_PATTERNS)


def _base_priority(pos: float, neg: float, pos_lex: float) -> str:
    margin = pos - neg
    if (margin >= 0.045 and pos >= 0.70) or (margin >= 0.020 and pos >= 0.69 and pos_lex >= 0.45):
        return "A"
    if (margin >= 0.020 and pos >= 0.68) or (margin >= 0.000 and pos >= 0.67 and pos_lex >= 0.28):
        return "B"
    if (margin >= 0.000 and pos >= 0.66) or (margin >= -0.015 and pos >= 0.66 and pos_lex >= 0.45):
        return "C"
    return "SKIP"


def _gate_priority(p: dict, priority: str, pos: float, neg: float, dscore: float, title_dscore: float, negative_name: str) -> tuple[str, str]:
    margin = pos - neg
    primary = p.get("primary_category", "") or ""
    reason = "contrastive semantic score"

    if not primary.startswith("astro-ph."):
        if dscore < 4.0 or margin < 0:
            return "SKIP", "non-astro primary without strong target-domain evidence"
        if priority == "A" and dscore < 6.0:
            priority, reason = "B", "non-astro primary capped at B"

    if primary in SECONDARY_ASTRO and dscore < 2.5 and priority in {"A", "B"}:
        if margin >= 0.035 and dscore >= 1.0:
            priority, reason = "C", "secondary astro category with weak target-object evidence"
        else:
            return "SKIP", "secondary astro category without direct target-object evidence"

    strong_negative = negative_name in {
        "solar_physics", "stellar_evolution", "planetary_disks", "compact_objects",
        "relativistic_plasma", "generic_mhd", "nuclear_particle", "generic_instrumentation",
    }
    if strong_negative and dscore < 2.0 and neg >= pos - 0.020:
        return "SKIP", "negative-domain match with weak target-object evidence"

    if priority in {"SKIP", "C"}:
        if _direct_title(p["title"]) and dscore >= 4.0 and pos >= 0.70 and margin >= -0.025:
            priority, reason = "B", "rescued by direct target-object title evidence"
        elif dscore >= 6.0 and pos >= 0.71 and margin >= -0.020:
            priority, reason = "C", "rescued by strong multi-signal target-domain evidence"

    if priority == "A" and dscore < 1.5 and title_dscore == 0:
        priority, reason = "C", "A capped: insufficient direct target-object evidence"
    return priority, reason


def _load_nli():
    print("[INFO] Loading zero-shot NLI model")
    tok = AutoTokenizer.from_pretrained(NLI_MODEL)
    model = AutoModelForSequenceClassification.from_pretrained(NLI_MODEL)
    model.eval()
    return model, tok


def _entailment_index(model) -> int:
    for idx, label in model.config.id2label.items():
        if "entail" in str(label).lower():
            return int(idx)
    return 1


def _nli_scores(model, tok, premises: list[str], labels: list[str], batch_size: int = 16) -> np.ndarray:
    hypotheses = [f"This paper is primarily about {label}." for label in labels]
    pairs = [(i, j, prem, hyp) for i, prem in enumerate(premises) for j, hyp in enumerate(hypotheses)]
    eidx = _entailment_index(model)
    logits = np.zeros((len(premises), len(labels)), dtype=float)
    with torch.no_grad():
        for start in range(0, len(pairs), batch_size):
            batch = pairs[start:start + batch_size]
            inputs = tok(
                [x[2] for x in batch], [x[3] for x in batch], padding=True,
                truncation=True, max_length=384, return_tensors="pt",
            )
            vals = model(**inputs).logits[:, eidx].cpu().numpy()
            for item, value in zip(batch, vals):
                logits[item[0], item[1]] = float(value)
    shifted = logits - logits.max(axis=1, keepdims=True)
    exp = np.exp(shifted)
    return exp / np.clip(exp.sum(axis=1, keepdims=True), 1e-12, None)


def _one_down(priority: str) -> str:
    return RANK_TO_PRIORITY[max(0, RANK[priority] - 1)]


def _nli_decision(p: dict, pos_score: float, neg_score: float) -> tuple[str, str]:
    old = p["priority"]
    dscore = float(p["domain_evidence_score"])
    title_d = float(p["title_domain_score"])
    primary = p.get("primary_category", "") or ""
    share = pos_score / max(pos_score + neg_score, 1e-12)
    strong_object = title_d >= 3.0 or dscore >= 8.0

    if old in {"A", "B"}:
        if share < 0.32 and not strong_object:
            return "SKIP", "NLI strongly favors a non-target domain"
        if share < 0.48 and not strong_object:
            return _one_down(old), "NLI weakens the target-domain interpretation"
        if share < 0.60 and dscore < 4.0 and title_d == 0.0:
            return _one_down(old), "weak NLI target preference without direct object evidence"
        if old == "A" and share < 0.50 and dscore < 6.0:
            return "B", "A capped because broad-domain classification is ambiguous"
        return old, "NLI and object evidence are consistent"

    if old == "C":
        if share >= 0.72 and (title_d >= 3.0 or dscore >= 6.0) and primary in {"astro-ph.GA", "astro-ph.SR", "astro-ph.HE"}:
            return "B", "NLI promotes a strongly evidenced target-domain candidate"
        if share < 0.28 and not strong_object:
            return "SKIP", "NLI strongly favors a non-target domain"
        return "C", "NLI leaves a boundary candidate unchanged"

    if old == "SKIP":
        if share >= 0.78 and title_d >= 3.0 and primary in {"astro-ph.GA", "astro-ph.SR", "astro-ph.HE"}:
            return "B", "NLI rescues a title-confirmed target-domain near-miss"
        if share >= 0.70 and (title_d >= 3.0 or (primary in {"astro-ph.GA", "astro-ph.SR"} and dscore >= 6.0)):
            return "C", "NLI rescues a conservative target-domain near-miss"
    return old, "NLI leaves the decision unchanged"


def _scope_calibrate(p: dict) -> tuple[str, str]:
    old = p["priority"]
    title = p["title"]
    text = title + "\n" + p["abstract"]
    h = lambda pat, s=text: re.search(pat, s, flags=re.I) is not None

    fermi_hi = h(r"Fermi Bubbles?") and h(r"\b(?:neutral gas|neutral clouds?|H\s*I\s+(?:data|clouds?|gas|emission)|N[_ ]?HI)\b")
    cmz_gas = h(r"\b(?:CMZ|Central Molecular Zone|Galactic Cent(?:re|er))\b") and h(r"\b(?:molecular|atomic|gas|cloud|feedback|turbulence|star formation)\b")
    interstellar_magnetic = re.search(
        r"\binterstellar\b.*\b(?:magnetic|reconnection|filament|gas|medium)\b|\b(?:magnetic|reconnection|filament)\b.*\binterstellar\b",
        title, flags=re.I,
    ) is not None
    explicit_hi_title = re.search(
        r"\b(?:neutral gas|neutral hydrogen|H\s*I\s+(?:clouds?|gas|emission|absorption|survey))\b",
        title, flags=re.I,
    ) is not None
    if old in {"SKIP", "C"} and (fermi_hi or cmz_gas or interstellar_magnetic or explicit_hi_title):
        return "B", "scope rescue: explicit Galactic/local ISM object"

    galactic_halo_gas_title = (
        re.search(r"\bGalactic halo\b", title, flags=re.I) is not None
        and re.search(r"\b(?:gas|H\s*I|neutral|ionized|clouds?|medium)\b", title, flags=re.I) is not None
    )
    halo_cgm_title = (
        re.search(
            r"\b(?:high[- ]velocity clouds?|HVCs?|intermediate[- ]velocity clouds?|IVCs?|Magellanic Stream|halo gas|circumgalactic medium|CGM|galactic fountain)\b",
            title, flags=re.I,
        ) is not None
        or galactic_halo_gas_title
    )
    if old in {"SKIP", "C"} and halo_cgm_title and float(p.get("domain_evidence_score", 0.0)) >= 4.0:
        return "B", "halo/CGM target rescue: explicit target object"

    highz = h(r"\b(?:high[- ]redshift|early Universe|reionization|z\s*[=~>]\s*[4-9])\b") and re.search(r"\bgalax(?:y|ies)\b", title, flags=re.I)
    if highz and not halo_cgm_title and old in {"A", "B"}:
        return "C", "scope cap: high-redshift galaxy-evolution focus"

    plural_galaxies = re.search(r"\bgalaxies\b", title, flags=re.I) is not None and re.search(r"\bGalactic\b", title, flags=re.I) is None
    direct_target_title = (
        re.search(
            r"\b(?:molecular cloud|interstellar medium|neutral hydrogen|H\s*I\s+(?:gas|cloud|emission)|dense core|H\s*II region|star[- ]forming region|high[- ]velocity cloud|HVC|circumgalactic medium|CGM|halo gas)\b",
            title, flags=re.I,
        ) is not None
        or galactic_halo_gas_title
    )
    if plural_galaxies and not direct_target_title and old in {"A", "B"}:
        return "C", "scope cap: broad external-galaxy sample"
    return old, "scope calibration unchanged"


def score_papers(papers: list[dict], config_path: str | Path = "semantic_topics.json") -> tuple[list[dict], dict]:
    """Score a daily set of arXiv papers and return sorted results plus summary."""
    if not papers:
        return [], {"candidate_count": 0, "A": 0, "B": 0, "C": 0, "SKIP": 0}

    cfg = json.loads(Path(config_path).read_text(encoding="utf-8"))
    positive = cfg["positive_topics"]
    negative = cfg["negative_topics"]
    pos_names = list(positive)
    neg_names = list(negative)

    model, tok = _load_specter2()
    model.set_active_adapters("adhoc_query")
    pos_q = _embed(model, tok, [positive[n]["description"] for n in pos_names], batch_size=8)
    neg_q = _embed(model, tok, [negative[n] for n in neg_names], batch_size=8)
    model.set_active_adapters("proximity")
    paper_e = _embed(model, tok, [p["title"] + tok.sep_token + p["abstract"] for p in papers], batch_size=16)
    pos_sem = _cosine(pos_q, paper_e)
    neg_sem = _cosine(neg_q, paper_e)
    pos_lex = _lexical_scores(papers, positive)

    results = []
    for i, p in enumerate(papers):
        pi = int(np.argmax(pos_sem[i]))
        ni = int(np.argmax(neg_sem[i]))
        pos = float(pos_sem[i, pi])
        neg = float(neg_sem[i, ni])
        lex = float(np.max(pos_lex[i]))
        base = _base_priority(pos, neg, lex)
        dscore, title_d, dhits = _domain_evidence(p)
        gated, gate_reason = _gate_priority(p, base, pos, neg, dscore, title_d, neg_names[ni])
        display = float(np.clip(50.0 + 450.0 * (pos - neg) + 12.0 * lex + min(dscore, 6.0) * 1.5, 0, 100))
        top = np.argsort(pos_sem[i])[::-1][:3]
        results.append({
            **p,
            "base_priority": base,
            "priority": gated,
            "score": round(display, 1),
            "best_positive_topic": pos_names[pi],
            "best_positive_semantic": round(pos, 4),
            "best_negative_topic": neg_names[ni],
            "best_negative_semantic": round(neg, 4),
            "semantic_margin": round(pos - neg, 4),
            "positive_lexical": round(lex, 4),
            "domain_evidence_score": round(dscore, 2),
            "title_domain_score": round(title_d, 2),
            "domain_hits": dhits,
            "gate_reason": gate_reason,
            "top_topics": [pos_names[int(j)] for j in top],
        })

    shortlist = [
        i for i, p in enumerate(results)
        if p["priority"] != "SKIP"
        or p["base_priority"] in {"A", "B"}
        or p["domain_evidence_score"] >= 4.0
        or p["title_domain_score"] >= 3.0
    ]
    if shortlist:
        nli_model, nli_tok = _load_nli()
        pos_labels = cfg["nli_positive_labels"]
        neg_labels = cfg["nli_negative_labels"]
        labels = pos_labels + neg_labels
        premises = [
            "Title: " + results[i]["title"] + "\nAbstract: " + results[i]["abstract"]
            for i in shortlist
        ]
        scores = _nli_scores(nli_model, nli_tok, premises, labels)
        npos = len(pos_labels)
        for local_i, paper_i in enumerate(shortlist):
            p = results[paper_i]
            ps = scores[local_i, :npos]
            ns = scores[local_i, npos:]
            pidx = int(np.argmax(ps))
            nidx = int(np.argmax(ns))
            pscore = float(ps[pidx])
            nscore = float(ns[nidx])
            share = pscore / max(pscore + nscore, 1e-12)
            before = p["priority"]
            after, reason = _nli_decision(p, pscore, nscore)
            p["pre_nli_priority"] = before
            p["priority"] = after
            p["nli_positive_label"] = pos_labels[pidx]
            p["nli_negative_label"] = neg_labels[nidx]
            p["nli_ism_share"] = round(share, 4)
            p["nli_reason"] = reason
            p["score"] = round(float(np.clip(0.70 * p["score"] + 0.30 * 100.0 * share, 0, 100)), 1)

    evaluated = set(shortlist)
    for i, p in enumerate(results):
        if i not in evaluated:
            p["pre_nli_priority"] = p["priority"]
            p["nli_positive_label"] = None
            p["nli_negative_label"] = None
            p["nli_ism_share"] = None
            p["nli_reason"] = "not evaluated: outside semantic/domain shortlist"

    for p in results:
        before = p["priority"]
        after, reason = _scope_calibrate(p)
        p["pre_scope_priority"] = before
        p["priority"] = after
        p["scope_reason"] = reason

    results.sort(key=lambda p: (RANK[p["priority"]], float(p["score"])), reverse=True)
    summary = {
        "candidate_count": len(results),
        "nli_evaluated": len(shortlist),
        "A": sum(p["priority"] == "A" for p in results),
        "B": sum(p["priority"] == "B" for p in results),
        "C": sum(p["priority"] == "C" for p in results),
        "SKIP": sum(p["priority"] == "SKIP" for p in results),
        "models": {"embedding": SPECTER_BASE, "nli": NLI_MODEL},
    }
    return results, summary
