"""Test: Does the strict grounding prompt eliminate cross-contamination at
realistic batch sizes (30-40 facts)?

If yes, we can remove the text-match guard entirely and save CPU.

Run:
    uv run --project libs/kt-facts python experiments/realistic_batch_test.py
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from kt_models.gateway import ModelGateway


STRICT_SYSTEM = """\
You are a precision entity extractor for a knowledge graph. You receive numbered facts.

For EACH fact, extract entities ONLY if their name (or a clear abbreviation/alias) \
appears as a SUBSTRING in the fact's text.

STRICT RULE: If you cannot find the entity's name or a known abbreviation literally \
written in the fact text, do NOT list it for that fact. "Relevant to the topic" is \
NOT sufficient — the name must be PRESENT IN THE TEXT.

Example:
- Fact: "NASA launched Apollo 11 in 1969" → entities: NASA, Apollo 11 ✓
- Fact: "The mission landed on the Moon" → entities: [] (no named entity in text) ✓
- Fact: "The mission landed on the Moon" → entities: NASA ✗ WRONG (NASA not in text)

Node types:
- "entity" = persons or organizations (set entity_subtype: person/organization/other)
- "concept" = abstract topics, ideas, techniques, publications
- "event" = time-bound occurrences
- "location" = physical places

Do NOT extract: author names from citations (e.g. "Smith et al."), journal names \
from citation context, DOIs, or publication metadata.

Return JSON: {{"facts": {{"1": [...], "2": [...], ...}}}}
Only the JSON, no fences."""

USER_TEMPLATE = """\
Here are {count} facts:

{fact_list}

For EACH fact, list entities/concepts it explicitly mentions. Format:
{{"facts": {{"1": [{{"name": "...", "node_type": "...", "entity_subtype": "person|organization|other"}}], ...}}}}"""

# 40 real facts from the DB
FACTS = [
    "Traditional medicine has a long history of contributing to conventional medicine and continues to hold promise.",
    "The accuracy of epitope prediction remains suboptimal, which necessitates the refinement of computational workflows.",
    "In Vermont, the Office of Professional Regulation (OPR) regulates naturopathic doctors (NDs).",
    "The aging of the population has shifted research focus toward chronic and degenerative diseases, which has increased drug development costs.",
    "Reiki and energy medicine are described by the Hobson Institute as working on physical, emotional, and spiritual levels.",
    "In a 2015 systematic review involving more than 500 patients with C. difficile infections, researchers with the VA and the University of Minnesota found that fecal transplantation was successful in 85 percent of patients.",
    "The researchers assumed that any increases in the signal from the modified GDV instrument would reflect enhanced conductivity of the skin.",
    "The ASTRO guideline for oropharyngeal squamous cell carcinoma treatment has been endorsed by ESTRO and ASCO.",
    "The RECORDS study, detailed by J. Fleuriet et al. in BMJ Open (2023), is a multicenter, placebo-controlled, biomarker-guided, adaptive Bayesian design basket trial.",
    "The pain-reducing effects of mindfulness meditation for low back pain are short-lived, according to a 2023 study by Schmidt and Pilat.",
    "El-Jawahri et al. (2021) studied patients with high-risk AML receiving intensive chemotherapy using integrated early palliative care.",
    "The Trendelenburg test evaluates hip abductors innervated by the L5 nerve root.",
    "Clinical procedure costs account for 15 to 22 percent of the total costs across all clinical trial phases.",
    "The immune system utilizes immunological barriers known as immune checkpoints to prevent immune cells from attacking healthy cells.",
    "The CRISPR/Cas9 system is used to engineer CAR-T cells, where T cells are directed to express cancer-antigen specific T Cell Receptors.",
    "Clinical features of disc herniations at the L1-L2 and L2-L3 levels were variable according to a 2010 study by DS Kim.",
    "Brody H authored 'The placebo effect: Implications for the Study and Practice of Complementary and Alternative Medicine' published in 2002.",
    "Volunteers at cancer care facilities undergo formal training on patient safety, communication, and HIPAA.",
    "Reiki therapy originated with Dr. Mikao Usui.",
    "Practitioners of biofield therapies may influence human electromagnetic fields through coherent heart rhythms and focused intention.",
    "The homeopathic integrative protocol at Campo di Marte Hospital involved administering Radium bromatum 6 CH before radiotherapy.",
    "A retrospective study by Dobzyniak et al. found no significant difference in infection risk between lumbar disc surgery groups.",
    "The high packing capacity of Lentiviruses allows for the capture of more gene-editing elements such as Prime Editors.",
    "Tumor cells subvert primed immune responses by increasing PD-L1 expression.",
    "Among the 43 metastatic melanoma patients who received COVID-19 mRNA vaccines, 21 received BNT162b2 and 22 received mRNA-1273.",
    "Kayoko Hosaka et al. discovered that KRAS-mutated epithelial cancers resist anti-angiogenic drugs by using ANG2.",
    "Kakumanu et al. (2019) investigated Vipassana meditation practice on P3 EEG dynamics, published in Progress in Brain Research.",
    "Tahoe Forest Cancer Center (TFCC) in Truckee, California, operates as a solo physician ambulatory oncology practice.",
    "Strategies to increase knowledge about Traditional and Complementary Medicine among conventional healthcare providers include integrating safety training.",
    "The high pricing of CAR-T therapies has sparked a debate regarding whether the clinical value justifies their cost.",
    "Unequal distribution of benefits occurs when clinical trial participation and disease burden are not geographically aligned.",
    "Liu J et al. published a review in Signal Transduction and Targeted Therapy (2022) detailing Wnt/β-catenin signalling.",
    "Bette Henson reports that she diligently wears her post-surgical brace and follows recovery directions.",
    "The first degree of Reiki training provides the necessary skills to facilitate healing in oneself or others.",
    "Dang, C. V. et al. published 'Drugging the undruggable cancer targets' in Nature Reviews Cancer in 2017.",
    "The regulatory environment in wealthy countries has become increasingly burdensome for drug sponsors.",
    "The scoping review evaluates Dignity Therapy in palliative care across settings including digital environments and pediatric populations.",
    "The 2006 HPRAC report framed its risk assessment for homeopathy using cultural safety principles.",
    "The ability of dendritic cells to reliably prime T cells in situ was identified as early as the 1990s.",
    "Specialist palliative care is frequently introduced late in the disease trajectory.",
]


def _entity_in_text(name: str, text: str) -> bool:
    text_lower = text.lower()
    if name.lower() in text_lower:
        return True
    tokens = name.split()
    if len(tokens) >= 2:
        surname = tokens[-1].lower()
        if len(surname) >= 3 and surname in text_lower:
            return True
    return False


async def main():
    gateway = ModelGateway()
    print(f"Model: {gateway.decomposition_model}")
    print(f"Facts: {len(FACTS)}")
    print()

    lines = [f"{i+1}. {f}" for i, f in enumerate(FACTS)]
    user_msg = USER_TEMPLATE.format(count=len(FACTS), fact_list="\n".join(lines))

    t0 = time.time()
    raw = await gateway.generate_json(
        model_id=gateway.decomposition_model,
        messages=[{"role": "user", "content": user_msg}],
        system_prompt=STRICT_SYSTEM,
        temperature=0.0,
        max_tokens=32000,
    )
    elapsed = time.time() - t0

    if not raw or not isinstance(raw, dict):
        print("ERROR: No result from LLM")
        return

    facts_data = raw.get("facts", {})
    if not isinstance(facts_data, dict):
        print(f"ERROR: Unexpected format: {list(raw.keys())}")
        return

    # Analyze entity-fact links
    total_entity_links = 0
    correct_links = 0
    wrong_links = 0
    wrong_examples: list[str] = []

    for fact_key, entities in facts_data.items():
        try:
            fact_idx = int(fact_key) - 1
        except (ValueError, TypeError):
            continue
        if fact_idx < 0 or fact_idx >= len(FACTS):
            continue

        fact_text = FACTS[fact_idx]
        if not isinstance(entities, list):
            continue

        for ent in entities:
            if not isinstance(ent, dict):
                continue
            name = ent.get("name", "").strip()
            ntype = ent.get("node_type", "concept")
            if not name:
                continue

            # Only check entity-type nodes (the problem area)
            if ntype != "entity":
                continue

            total_entity_links += 1
            if _entity_in_text(name, fact_text):
                correct_links += 1
            else:
                wrong_links += 1
                wrong_examples.append(
                    f"  WRONG: '{name}' tagged on fact {fact_idx+1}: '{fact_text[:80]}...'"
                )

    print(f"Time: {elapsed:.1f}s")
    print(f"Total entity-fact links: {total_entity_links}")
    print(f"Correct (entity in text): {correct_links}")
    print(f"WRONG (entity NOT in text): {wrong_links}")
    if total_entity_links > 0:
        precision = correct_links / total_entity_links
        print(f"Precision: {precision:.1%}")
    print()

    if wrong_examples:
        print("Cross-contamination examples:")
        for ex in wrong_examples[:10]:
            print(ex)
    else:
        print("ZERO cross-contamination detected!")
        print("The strict grounding prompt eliminates the problem at batch_size=40.")
        print("The text-match guard can be safely removed.")


if __name__ == "__main__":
    asyncio.run(main())
