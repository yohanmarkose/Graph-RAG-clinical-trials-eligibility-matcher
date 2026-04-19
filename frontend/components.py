from __future__ import annotations

import streamlit as st

# ---------------------------------------------------------------------------
# Lookup tables — condition names → SNOMED IDs, drug names → RxNorm CUIs
# ---------------------------------------------------------------------------

CONDITION_TO_SNOMED: dict[str, str] = {
    # Oncology
    "Breast Cancer": "254837009",
    "HER2-Positive Breast Cancer": "427685000",
    "Metastatic Breast Cancer": "408643008",
    "Triple-Negative Breast Cancer": "31509003",
    "ER-Positive Breast Cancer": "396533008",
    "Non-Small Cell Lung Cancer": "254637007",
    "Small Cell Lung Cancer": "413448000",
    "Colorectal Cancer": "363406005",
    "Metastatic Colorectal Cancer": "94260004",
    "Melanoma": "372244006",
    "Prostate Cancer": "399068003",
    "Leukemia": "93143009",
    "Chronic Lymphocytic Leukemia": "92814006",
    "Lymphoma": "118600007",
    "Ovarian Cancer": "363443007",
    "Pancreatic Cancer": "372003004",
    "Renal Cell Carcinoma": "41607009",
    "Glioblastoma": "393563007",
    "Bladder Cancer": "93144003",
    "Endometrial Cancer": "371973000",
    "Head and Neck Squamous Cell Carcinoma": "420620002",
    # Cardiology
    "Heart Failure": "84114007",
    "Atrial Fibrillation": "49436004",
    "Coronary Artery Disease": "53741008",
    "Hypertension": "38341003",
    # Neurology
    "Alzheimer's Disease": "26929004",
    "Parkinson's Disease": "49049000",
    "Multiple Sclerosis": "24700007",
    "Epilepsy": "84757009",
    # Endocrinology
    "Type 2 Diabetes": "44054006",
    "Type 1 Diabetes": "46635009",
    # Immunology
    "Rheumatoid Arthritis": "69896004",
    "Lupus": "55464009",
    # Pulmonology
    "COPD": "13645005",
    "Asthma": "195967001",
    # Psychiatry
    "Major Depression": "370143000",
    "Bipolar Disorder": "13746004",
    "Schizophrenia": "58214004",
    # Infectious Disease
    "HIV/AIDS": "86406008",
}

DRUG_TO_RXNORM: dict[str, str] = {
    "trastuzumab": "224905",
    "pertuzumab": "1298944",
    "t-dm1": "1371046",
    "ado-trastuzumab emtansine": "1371046",
    "osimertinib": "1860477",
    "erlotinib": "352962",
    "gefitinib": "352707",
    "bevacizumab": "416914",
    "pembrolizumab": "1643507",
    "nivolumab": "1547545",
    "olaparib": "1597582",
    "oxaliplatin": "151399",
    "fluorouracil": "4492",
    "5-fu": "4492",
    "irinotecan": "51499",
    "imatinib": "282388",
    "rituximab": "121191",
    "cetuximab": "318341",
    "docetaxel": "72962",
    "paclitaxel": "56946",
    "carboplatin": "38786",
    "cisplatin": "2555",
    "gemcitabine": "51267",
    "pemetrexed": "343072",
    "capecitabine": "194000",
    "palbociclib": "1873984",
    "ribociclib": "1859145",
    "fulvestrant": "203239",
    "dabrafenib": "1860490",
    "trametinib": "1733984",
    "sunitinib": "406222",
    "ibrutinib": "1454898",
    "niraparib": "1860484",
    "durvalumab": "1908197",
    "sacituzumab govitecan": "2390663",
    "dostarlimab": "2390001",
    "atezolizumab": "1859164",
    "tamoxifen": "41493",
    "letrozole": "72251",
}

CONDITIONS_BY_AREA: dict[str, list[str]] = {
    "All": sorted(CONDITION_TO_SNOMED.keys()),
    "Oncology": [
        "Breast Cancer", "HER2-Positive Breast Cancer", "Metastatic Breast Cancer",
        "Triple-Negative Breast Cancer", "ER-Positive Breast Cancer",
        "Non-Small Cell Lung Cancer", "Small Cell Lung Cancer",
        "Colorectal Cancer", "Metastatic Colorectal Cancer",
        "Melanoma", "Prostate Cancer",
        "Leukemia", "Chronic Lymphocytic Leukemia", "Lymphoma",
        "Ovarian Cancer", "Pancreatic Cancer",
        "Renal Cell Carcinoma", "Glioblastoma", "Bladder Cancer",
        "Endometrial Cancer", "Head and Neck Squamous Cell Carcinoma",
    ],
    "Cardiology": ["Heart Failure", "Atrial Fibrillation", "Coronary Artery Disease", "Hypertension"],
    "Neurology": ["Alzheimer's Disease", "Parkinson's Disease", "Multiple Sclerosis", "Epilepsy"],
    "Endocrinology": ["Type 2 Diabetes", "Type 1 Diabetes"],
    "Immunology": ["Rheumatoid Arthritis", "Lupus"],
    "Pulmonology": ["COPD", "Asthma"],
    "Psychiatry": ["Major Depression", "Bipolar Disorder", "Schizophrenia"],
    "Infectious Disease": ["HIV/AIDS"],
    "Other": sorted(CONDITION_TO_SNOMED.keys()),
}


# ---------------------------------------------------------------------------
# Score helpers
# ---------------------------------------------------------------------------

def score_color(score: float) -> str:
    if score >= 80:
        return "#28a745"
    if score >= 50:
        return "#e6a817"
    return "#dc3545"


def score_emoji(score: float) -> str:
    if score >= 80:
        return "🟢"
    if score >= 50:
        return "🟡"
    return "🔴"


# ---------------------------------------------------------------------------
# Score breakdown
# ---------------------------------------------------------------------------

_BREAKDOWN_META = {
    "condition":     ("Condition Match",  40),
    "biomarker":     ("Biomarker Match",  25),
    "prior_therapy": ("Prior Therapy",    15),
    "demographics":  ("Demographics",     10),
    "trial_quality": ("Trial Quality",    10),
}


def render_score_breakdown(breakdown: dict) -> None:
    """Render per-category scores as labeled progress bars."""
    for key, (label, max_pts) in _BREAKDOWN_META.items():
        data = breakdown.get(key, {})
        earned = float(data.get("earned", 0))
        possible = float(data.get("possible", max_pts))
        pct = min(earned / possible, 1.0) if possible > 0 else 0.0
        bar_col, val_col = st.columns([5, 1])
        bar_col.progress(pct, text=label)
        val_col.markdown(
            f"<div style='text-align:right; padding-top:6px; font-size:0.85em;'>"
            f"<b>{earned:.0f}</b>/{possible:.0f}</div>",
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Trial card
# ---------------------------------------------------------------------------

def render_trial_card(match: dict, idx: int) -> None:
    """Render a single trial match as an expandable card."""
    score = match.get("score", 0)
    title = match.get("title", "Unknown Trial")
    nct_id = match.get("nct_id", "")
    phase = match.get("phase", "?")
    status = match.get("status", "?")
    sponsor = match.get("sponsor", "?")
    url = match.get("url") or f"https://clinicaltrials.gov/study/{nct_id}"
    explanation = match.get("explanation", "")

    expander_label = f"{score_emoji(score)} {score:.0f}/100 — {title}"

    with st.expander(expander_label, expanded=(idx == 0)):
        meta_col, exp_col = st.columns([2, 3])

        with meta_col:
            st.markdown(f"**NCT ID:** [{nct_id}]({url})")
            st.markdown(
                f"**Phase:** {phase} &nbsp;|&nbsp; "
                f"**Status:** {status}"
            )
            st.markdown(f"**Sponsor:** {sponsor}")
            st.markdown("---")
            st.markdown("##### Score Breakdown")
            render_score_breakdown(match.get("score_breakdown", {}))

        with exp_col:
            st.markdown("##### Match Explanation")
            if explanation:
                st.markdown(explanation)
            else:
                st.info("No LLM explanation available.")

        # Criteria sub-expander
        matched = match.get("matched_criteria", [])
        unmatched = match.get("unmatched_criteria", [])
        if matched or unmatched:
            with st.expander("View Full Criteria"):
                if matched:
                    st.markdown("**Matched:**")
                    for c in matched:
                        st.markdown(f"  ✓ {_fmt_criterion(c)}")
                if unmatched:
                    st.markdown("**Not Matched / Unknown:**")
                    for c in unmatched:
                        st.markdown(f"  ? {_fmt_criterion(c)}")


def _fmt_criterion(c: dict) -> str:
    if "term" in c:
        return f"{c['term']} (SNOMED {c.get('concept_id', '?')})"
    if "name" in c and "status" in c:
        return f"{c['name']} {c.get('status', '')} (patient: {c.get('patient_status', '?')})"
    if "rxcui" in c:
        return f"{c.get('name', c['rxcui'])} (RxNorm)"
    return str(c)


# ---------------------------------------------------------------------------
# Extracted patient profile card
# ---------------------------------------------------------------------------

def render_patient_summary(profile: dict) -> None:
    """Render a PatientProfile dict as a confirmation info box."""
    lines: list[str] = []
    if profile.get("age"):
        lines.append(f"**Age:** {profile['age']}")
    if profile.get("gender"):
        lines.append(f"**Gender:** {profile['gender']}")
    if profile.get("condition_names"):
        lines.append(f"**Conditions:** {', '.join(profile['condition_names'])}")
    if profile.get("biomarkers"):
        bm = ", ".join(
            f"{b['name']} ({b['status']})" for b in profile["biomarkers"]
        )
        lines.append(f"**Biomarkers:** {bm}")
    if profile.get("prior_therapy_names"):
        lines.append(f"**Prior Therapies:** {', '.join(profile['prior_therapy_names'])}")
    if profile.get("ecog_status") is not None:
        lines.append(f"**ECOG:** {profile['ecog_status']}")
    if profile.get("therapeutic_area"):
        lines.append(f"**Area:** {profile['therapeutic_area']}")

    if lines:
        st.info("  \n".join(lines))
