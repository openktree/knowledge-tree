"""Categorized test pairs for seed dedup threshold tuning.

Each pair is a named tuple with:
  - name_a, name_b: the two seed names
  - should_merge: True (same entity), False (different), None (unknown)
  - category: grouping key for reporting
  - notes: why this pair is interesting
"""

from __future__ import annotations

from typing import NamedTuple


class SeedPair(NamedTuple):
    name_a: str
    name_b: str
    should_merge: bool | None
    category: str
    notes: str


# ── Typos: misspellings that SHOULD merge ──────────────────────────────

TYPO_PAIRS: list[SeedPair] = [
    SeedPair("Democratic Party", "Democrtic Party", True, "typo", "missing letter"),
    SeedPair("Albert Einstein", "Alber Einstein", True, "typo", "missing trailing letter"),
    SeedPair("Jeffrey Epstein", "Jeffery Epstein", True, "typo", "vowel swap"),
    SeedPair("Mississippi", "Mississipi", True, "typo", "double letter reduction"),
    SeedPair("Barack Obama", "Barrack Obama", True, "typo", "extra letter"),
    SeedPair("Tchaikovsky", "Tchaikovksy", True, "typo", "letter transposition"),
    SeedPair("Dostoevsky", "Dostoyevsky", True, "typo", "transliteration variant"),
    SeedPair("Muhammad Ali", "Mohammed Ali", True, "typo", "transliteration"),
    SeedPair("Gaddafi", "Qaddafi", True, "typo", "transliteration of Arabic"),
    SeedPair("Beijing", "Peking", True, "typo", "romanization variant"),
]

# ── Different entities: same domain but distinct, should NOT merge ──────

DIFFERENT_ENTITY_PAIRS: list[SeedPair] = [
    # Universities
    SeedPair("Arizona State University", "The University of Arizona", False, "different_entity", "the original bug"),
    SeedPair("University of California", "University of Michigan", False, "different_entity", "same prefix"),
    SeedPair("MIT", "Caltech", False, "different_entity", "both tech schools"),
    SeedPair("Harvard University", "Harvard Business School", False, "different_entity", "parent vs subdivision"),
    # Banks
    SeedPair("Bank of America", "Bank of England", False, "different_entity", "same structure"),
    SeedPair("Goldman Sachs", "Morgan Stanley", False, "different_entity", "both investment banks"),
    # Wars
    SeedPair("World War 1", "World War 2", False, "different_entity", "sequential events"),
    SeedPair("Korean War", "Vietnam War", False, "different_entity", "different wars"),
    # Political parties
    SeedPair("Republican Party", "Democratic Party", False, "different_entity", "opposing parties"),
    # Courts
    SeedPair(
        "Supreme Court of the United States",
        "Supreme Court of Canada",
        False,
        "different_entity",
        "same institution different country",
    ),
    # Media
    SeedPair("New York Times", "New York Post", False, "different_entity", "same city different paper"),
    SeedPair("BBC", "CNN", False, "different_entity", "both news networks"),
    # People with similar names
    SeedPair("Ghislaine Maxwell", "Robert Maxwell", False, "different_entity", "father and daughter"),
    SeedPair("George H.W. Bush", "George W. Bush", False, "different_entity", "father and son presidents"),
]

# ── Aliases: abbreviations/titles that SHOULD merge ─────────────────────

ALIAS_PAIRS: list[SeedPair] = [
    SeedPair("Les Wexner", "Leslie Wexner", True, "alias", "short name"),
    SeedPair("Albert Einstein", "A. Einstein", True, "alias", "initial"),
    SeedPair("Barack Obama", "President Barack Obama", True, "alias", "title prefix"),
    SeedPair("The Miami Herald", "Miami Herald", True, "alias", "article prefix"),
    SeedPair("McDonald's", "McDonalds", True, "alias", "punctuation"),
    SeedPair("JP Morgan", "JPMorgan Chase", None, "alias", "informal name"),
]

# ── Acronyms: abbreviation <-> expansion pairs ─────────────────────────

ACRONYM_PAIRS: list[SeedPair] = [
    # Positive: acronym matches its expansion
    SeedPair("FBI", "Federal Bureau of Investigation", True, "acronym", "standard acronym"),
    SeedPair("CIA", "Central Intelligence Agency", True, "acronym", "standard acronym"),
    SeedPair("USA", "United States of America", True, "acronym", "country acronym"),
    SeedPair("NYSE", "New York Stock Exchange", True, "acronym", "financial acronym"),
    SeedPair("NATO", "North Atlantic Treaty Organization", True, "acronym", "military alliance"),
    SeedPair("NASA", "National Aeronautics and Space Administration", True, "acronym", "space agency"),
    SeedPair("WHO", "World Health Organization", True, "acronym", "UN agency"),
    SeedPair("UNICEF", "United Nations Children's Fund", True, "acronym", "UN fund — historical expansion"),
    SeedPair("SEC", "Securities and Exchange Commission", True, "acronym", "financial regulator"),
    SeedPair("NIH", "National Institutes of Health", True, "acronym", "health research"),
    SeedPair("GDP", "Gross Domestic Product", True, "acronym", "economic concept"),
    SeedPair("DNA", "Deoxyribonucleic Acid", True, "acronym", "biological concept"),
    SeedPair("AI", "Artificial Intelligence", True, "acronym", "tech concept"),
    SeedPair("EU", "European Union", True, "acronym", "political union"),
    SeedPair("IMF", "International Monetary Fund", True, "acronym", "financial institution"),
    # Negative: same acronym or similar acronym, different entity
    SeedPair("MIT", "Ministry of Information Technology", None, "acronym", "MIT could be either — ambiguous acronym"),
    SeedPair("SEC", "Southeastern Conference", None, "acronym", "SEC could be either — ambiguous acronym"),
    SeedPair("CIA", "Culinary Institute of America", None, "acronym", "CIA could be either — ambiguous acronym"),
    SeedPair("FBI", "Food and Beverage Industry", None, "acronym", "FBI could match initials — ambiguous"),
    SeedPair("WHO", "The Who", False, "acronym", "WHO vs the rock band"),
    SeedPair("CDC", "CIA", False, "acronym", "different agencies"),
    SeedPair("NATO", "NASA", False, "acronym", "different organizations"),
    SeedPair("NIH", "NSF", False, "acronym", "different research agencies"),
    SeedPair("EU", "AU", False, "acronym", "European Union vs African Union"),
    SeedPair("IMF", "World Bank", False, "acronym", "different international finance orgs"),
    SeedPair("GDP", "GNP", False, "acronym", "different economic measures"),
    SeedPair("DNA", "RNA", False, "acronym", "different nucleic acids"),
    SeedPair("AI", "ML", False, "acronym", "artificial intelligence vs machine learning"),
    SeedPair("UNESCO", "UNICEF", False, "acronym", "different UN agencies"),
    SeedPair("NAFTA", "NATO", False, "acronym", "trade agreement vs military alliance"),
]

# ── Person variants: same person, different name forms ──────────────────

PERSON_VARIANT_PAIRS: list[SeedPair] = [
    # Positive: same person
    SeedPair("JFK", "John F. Kennedy", True, "person_variant", "initials to full name"),
    SeedPair("FDR", "Franklin Delano Roosevelt", True, "person_variant", "initials to full name"),
    SeedPair("MLK", "Martin Luther King Jr.", True, "person_variant", "initials to full name"),
    SeedPair("RBG", "Ruth Bader Ginsburg", True, "person_variant", "initials to full name"),
    SeedPair("LBJ", "Lyndon Baines Johnson", True, "person_variant", "initials to full name"),
    SeedPair("Elon Musk", "Musk", True, "person_variant", "last name only"),
    SeedPair("Dr. Martin Luther King Jr.", "Martin Luther King Jr.", True, "person_variant", "title prefix"),
    SeedPair("Pope Francis", "Jorge Mario Bergoglio", True, "person_variant", "papal name vs birth name"),
    SeedPair("Mark Twain", "Samuel Clemens", True, "person_variant", "pen name vs real name"),
    SeedPair("Mahatma Gandhi", "Mohandas Karamchand Gandhi", True, "person_variant", "honorific vs full name"),
    # Negative: different people
    SeedPair("John F. Kennedy", "Robert F. Kennedy", False, "person_variant", "brothers"),
    SeedPair("George H.W. Bush", "Jeb Bush", False, "person_variant", "father and son"),
    SeedPair(
        "Martin Luther King Jr.", "Martin Luther", False, "person_variant", "civil rights leader vs Protestant reformer"
    ),
    SeedPair("John Adams", "John Quincy Adams", False, "person_variant", "father and son presidents"),
    SeedPair("Franklin Roosevelt", "Theodore Roosevelt", False, "person_variant", "different Roosevelt presidents"),
    SeedPair("Bill Clinton", "Hillary Clinton", False, "person_variant", "husband and wife"),
    SeedPair("Prince William", "Prince Harry", False, "person_variant", "brothers"),
    SeedPair("Kim Jong-un", "Kim Jong-il", False, "person_variant", "father and son"),
    SeedPair("Albert Einstein", "Niels Bohr", False, "person_variant", "different physicists"),
    SeedPair("Elon Musk", "Jeff Bezos", False, "person_variant", "different tech billionaires"),
]

# ── Organization variants: same org, different name forms ───────────────

ORG_VARIANT_PAIRS: list[SeedPair] = [
    # Positive: same org
    SeedPair("Google", "Alphabet Inc.", True, "org_variant", "subsidiary vs parent"),
    SeedPair("JPMorgan Chase", "JP Morgan", True, "org_variant", "formal vs informal"),
    SeedPair("McKinsey & Company", "McKinsey", True, "org_variant", "full vs short"),
    SeedPair("PricewaterhouseCoopers", "PwC", True, "org_variant", "full name vs abbreviation"),
    SeedPair("International Business Machines", "IBM", True, "org_variant", "full name vs acronym"),
    SeedPair("Bayerische Motoren Werke", "BMW", True, "org_variant", "German name vs acronym"),
    SeedPair("Facebook", "Meta Platforms", True, "org_variant", "old name vs rebrand"),
    SeedPair("General Electric", "GE", True, "org_variant", "full vs abbreviation"),
    SeedPair("Hewlett-Packard", "HP", True, "org_variant", "full vs abbreviation"),
    SeedPair("British Broadcasting Corporation", "BBC", True, "org_variant", "full vs acronym"),
    # Negative: different orgs
    SeedPair("Apple Inc.", "Apple Records", False, "org_variant", "tech company vs record label"),
    SeedPair("Amazon", "Amazon River", False, "org_variant", "company vs geographic feature"),
    SeedPair("Ford Motor Company", "Ford Foundation", False, "org_variant", "car company vs foundation"),
    SeedPair("Bank of America", "Bank of England", False, "org_variant", "different banks"),
    SeedPair("Goldman Sachs", "JPMorgan Chase", False, "org_variant", "different investment banks"),
    SeedPair("Google", "Microsoft", False, "org_variant", "different tech companies"),
    SeedPair("Red Cross", "Red Crescent", False, "org_variant", "different humanitarian orgs"),
    SeedPair("Harvard University", "Yale University", False, "org_variant", "different universities"),
    SeedPair("BP", "Shell", False, "org_variant", "different oil companies"),
    SeedPair("NBC", "CBS", False, "org_variant", "different TV networks"),
]

# ── Location variants: same place, different names ──────────────────────

LOCATION_VARIANT_PAIRS: list[SeedPair] = [
    # Positive: same place
    SeedPair("Myanmar", "Burma", True, "location_variant", "current vs historical name"),
    SeedPair("Persia", "Iran", True, "location_variant", "historical vs modern name"),
    SeedPair("NYC", "New York City", True, "location_variant", "abbreviation"),
    SeedPair("LA", "Los Angeles", True, "location_variant", "abbreviation"),
    SeedPair("D.C.", "District of Columbia", True, "location_variant", "abbreviation"),
    SeedPair("UK", "United Kingdom", True, "location_variant", "abbreviation"),
    SeedPair("Bombay", "Mumbai", True, "location_variant", "colonial vs modern name"),
    SeedPair("Constantinople", "Istanbul", True, "location_variant", "historical vs modern name"),
    # Negative: different places
    SeedPair("Washington D.C.", "Washington State", False, "location_variant", "city vs state"),
    SeedPair("North Korea", "South Korea", False, "location_variant", "different countries"),
    SeedPair("New York City", "New York State", False, "location_variant", "city vs state"),
    SeedPair("Portland, Oregon", "Portland, Maine", False, "location_variant", "same name different state"),
    SeedPair("Cambridge, MA", "Cambridge, UK", False, "location_variant", "same name different country"),
    SeedPair("Georgia (country)", "Georgia (US state)", False, "location_variant", "country vs state"),
    SeedPair("Sydney", "Melbourne", False, "location_variant", "different Australian cities"),
    SeedPair("Paris", "London", False, "location_variant", "different European capitals"),
]

# ── Concept synonyms: same concept, different terms ─────────────────────

CONCEPT_SYNONYM_PAIRS: list[SeedPair] = [
    # Positive: same concept
    SeedPair("AI", "artificial intelligence", True, "concept_synonym", "acronym for concept"),
    SeedPair("ML", "machine learning", True, "concept_synonym", "acronym for concept"),
    SeedPair("GDP", "gross domestic product", True, "concept_synonym", "economic concept acronym"),
    SeedPair("DNA", "deoxyribonucleic acid", True, "concept_synonym", "biology acronym"),
    SeedPair("global warming", "climate change", True, "concept_synonym", "near-synonyms"),
    SeedPair("heart attack", "myocardial infarction", True, "concept_synonym", "common vs medical term"),
    SeedPair("PTSD", "post-traumatic stress disorder", True, "concept_synonym", "medical acronym"),
    SeedPair("ADHD", "attention deficit hyperactivity disorder", True, "concept_synonym", "medical acronym"),
    # Negative: related but distinct concepts
    SeedPair("machine learning", "deep learning", False, "concept_synonym", "general vs specific"),
    SeedPair("artificial intelligence", "robotics", False, "concept_synonym", "overlapping but distinct"),
    SeedPair("capitalism", "communism", False, "concept_synonym", "opposing economic systems"),
    SeedPair("physics", "chemistry", False, "concept_synonym", "different sciences"),
    SeedPair("democracy", "republic", False, "concept_synonym", "different political systems"),
    SeedPair("DNA", "RNA", False, "concept_synonym", "different nucleic acids"),
    SeedPair("psychology", "psychiatry", False, "concept_synonym", "related but different fields"),
    SeedPair("recession", "depression", False, "concept_synonym", "different economic severities"),
]

# ── Containment: one name contains the other ────────────────────────────

CONTAINMENT_PAIRS: list[SeedPair] = [
    SeedPair("Jeffrey Epstein", "Jeffrey Epstein's Lawyer", False, "containment", "possessive extension"),
    SeedPair("Mars", "Mars Rover", False, "containment", "noun extension"),
    SeedPair(
        "House Oversight Committee",
        "Democrats on the House Oversight Committee",
        False,
        "containment",
        "qualifier prefix",
    ),
    SeedPair("Giuffre", "Virginia Roberts Giuffre", False, "containment", "last name vs full name — different context"),
    SeedPair("Python", "Python Programming Language", False, "containment", "ambiguous base word"),
    SeedPair("Apple", "Apple Inc.", None, "containment", "company vs fruit — context dependent"),
]

# ── Subtle: tricky edge cases ───────────────────────────────────────────

SUBTLE_PAIRS: list[SeedPair] = [
    SeedPair("Robert Kennedy", "Robert De Niro", False, "subtle", "same first name only"),
    SeedPair("Democratic Party", "Democrat Party", None, "subtle", "informal pejorative variant"),
    SeedPair("United Kingdom", "United States", False, "subtle", "both start with United"),
    SeedPair("North Korea", "South Korea", False, "subtle", "opposite halves"),
    SeedPair("European Union", "African Union", False, "subtle", "same structure different continent"),
    SeedPair(
        "2006 arrest of Jeffrey Epstein",
        "July 6 2019 arrest of Jeffrey Epstein",
        False,
        "subtle",
        "different dates same event type",
    ),
]

# ── Negative battery: cross-category false-positive traps ───────────────

NEGATIVE_BATTERY_PAIRS: list[SeedPair] = [
    # Names that share words but are completely different entities
    SeedPair("United Nations", "United Airlines", False, "negative_battery", "shared 'United'"),
    SeedPair("General Motors", "General Electric", False, "negative_battery", "shared 'General'"),
    SeedPair("American Airlines", "American Express", False, "negative_battery", "shared 'American'"),
    SeedPair("National Guard", "National Geographic", False, "negative_battery", "shared 'National'"),
    SeedPair(
        "International Court of Justice",
        "International Monetary Fund",
        False,
        "negative_battery",
        "shared 'International'",
    ),
    # Similar sounding but different
    SeedPair("Iran", "Iraq", False, "negative_battery", "similar-sounding countries"),
    SeedPair("Austria", "Australia", False, "negative_battery", "commonly confused countries"),
    SeedPair("Sweden", "Switzerland", False, "negative_battery", "commonly confused countries"),
    SeedPair("Slovakia", "Slovenia", False, "negative_battery", "commonly confused countries"),
    # Same domain, different entities
    SeedPair("White House", "Capitol Building", False, "negative_battery", "different US government buildings"),
    SeedPair("Senate", "House of Representatives", False, "negative_battery", "different legislative chambers"),
    SeedPair("FBI", "CIA", False, "negative_battery", "different intelligence agencies"),
    SeedPair("Tesla", "Edison", False, "negative_battery", "rival inventors"),
    SeedPair("Tesla Inc.", "Tesla (inventor)", False, "negative_battery", "company vs person"),
    SeedPair("SpaceX", "Blue Origin", False, "negative_battery", "different space companies"),
    # Acronyms that could be confused
    SeedPair("WHO", "WTO", False, "negative_battery", "different international orgs"),
    SeedPair("NATO", "NAFTA", False, "negative_battery", "military vs trade"),
    SeedPair("OPEC", "OECD", False, "negative_battery", "different economic orgs"),
    SeedPair("EPA", "FDA", False, "negative_battery", "different US agencies"),
    SeedPair("NSA", "NSF", False, "negative_battery", "security vs science"),
    # People with same last name
    SeedPair("Isaac Newton", "Wayne Newton", False, "negative_battery", "physicist vs entertainer"),
    SeedPair("Charles Darwin", "Charles Dickens", False, "negative_battery", "same first name different person"),
    SeedPair("Marie Curie", "Pierre Curie", False, "negative_battery", "wife and husband scientists"),
    SeedPair("John F. Kennedy", "Ted Kennedy", False, "negative_battery", "different Kennedy brothers"),
    # Concepts that share key words
    SeedPair("quantum mechanics", "quantum computing", False, "negative_battery", "different quantum fields"),
    SeedPair("civil war", "civil rights", False, "negative_battery", "shared 'civil'"),
    SeedPair("nuclear energy", "nuclear weapons", False, "negative_battery", "different nuclear applications"),
    SeedPair("space station", "space shuttle", False, "negative_battery", "different spacecraft"),
    SeedPair("climate change", "regime change", False, "negative_battery", "different kinds of 'change'"),
    SeedPair(
        "artificial intelligence",
        "emotional intelligence",
        False,
        "negative_battery",
        "different kinds of 'intelligence'",
    ),
]


# ── Embedding ambiguity: high cosine similarity but different concepts ─────

EMBEDDING_AMBIGUITY_PAIRS: list[SeedPair] = [
    # Different concepts with high embedding similarity
    SeedPair(
        "light-dependent reactions",
        "light-independent reactions",
        False,
        "embedding_ambiguity",
        "opposing photosynthesis phases",
    ),
    SeedPair(
        "light-dependent reactions",
        "light-dependent reactions of photosynthesis",
        True,
        "embedding_ambiguity",
        "same concept, more specific",
    ),
    SeedPair("oxidation", "reduction", False, "embedding_ambiguity", "opposing chemical processes"),
    SeedPair("anabolism", "catabolism", False, "embedding_ambiguity", "opposing metabolic processes"),
    SeedPair(
        "endothermic reaction", "exothermic reaction", False, "embedding_ambiguity", "opposing thermodynamic types"
    ),
    SeedPair("transcription", "translation", False, "embedding_ambiguity", "different gene expression steps"),
    SeedPair("mitosis", "meiosis", False, "embedding_ambiguity", "different cell division types"),
    SeedPair("supply", "demand", False, "embedding_ambiguity", "opposing economic forces"),
    SeedPair("inflation", "deflation", False, "embedding_ambiguity", "opposing price movements"),
    SeedPair("Calvin cycle", "Calvin-Benson cycle", True, "embedding_ambiguity", "same cycle, variant name"),
    SeedPair("photosynthesis", "oxygenic photosynthesis", True, "embedding_ambiguity", "general vs specific"),
]


# ── Convenience: all pairs flat ─────────────────────────────────────────

ALL_PAIRS: list[SeedPair] = (
    TYPO_PAIRS
    + DIFFERENT_ENTITY_PAIRS
    + ALIAS_PAIRS
    + CONTAINMENT_PAIRS
    + SUBTLE_PAIRS
    + ACRONYM_PAIRS
    + PERSON_VARIANT_PAIRS
    + ORG_VARIANT_PAIRS
    + LOCATION_VARIANT_PAIRS
    + CONCEPT_SYNONYM_PAIRS
    + NEGATIVE_BATTERY_PAIRS
    + EMBEDDING_AMBIGUITY_PAIRS
)
