"""Clinical Trial Eligibility Matcher — Streamlit demo UI.

Run with:
    streamlit run frontend/app.py
"""

from __future__ import annotations

import os

import requests
import streamlit as st

from frontend.components import (
    CONDITIONS_BY_AREA,
    CONDITION_TO_SNOMED,
    DRUG_TO_RXNORM,
    render_patient_summary,
    render_trial_card,
)

# ---------------------------------------------------------------------------
# Page config (must be first Streamlit call)
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Clinical Trial Matcher",
    page_icon="🧬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# API URL
# ---------------------------------------------------------------------------

def _api_url() -> str:
    try:
        return st.secrets.get("api_url", "http://localhost:8000")
    except Exception:
        return os.environ.get("API_URL", "http://localhost:8000")

API_URL = _api_url()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AREAS = [
    "All", "Oncology", "Cardiology", "Neurology", "Endocrinology",
    "Immunology", "Infectious Disease", "Pulmonology", "Psychiatry", "Other",
]
GENDERS = ["Unknown", "Female", "Male", "Other"]
ECOG_OPTS = ["Unknown", "0", "1", "2", "3", "4"]

# ---------------------------------------------------------------------------
# Demo presets
# ---------------------------------------------------------------------------

DEMOS: dict[str, dict] = {
    "HER2+ Breast Cancer": {
        "age": 58,
        "gender": "Female",
        "conditions": ["HER2-Positive Breast Cancer"],
        "biomarkers": "HER2=positive",
        "prior_therapies": "trastuzumab",
        "ecog": "1",
        "area": "Oncology",
        "freetext": (
            "58-year-old woman with HER2-positive metastatic breast cancer. "
            "Previously treated with trastuzumab. ECOG performance status 1."
        ),
    },
    "EGFR+ NSCLC": {
        "age": 65,
        "gender": "Male",
        "conditions": ["Non-Small Cell Lung Cancer"],
        "biomarkers": "EGFR=positive",
        "prior_therapies": "",
        "ecog": "0",
        "area": "Oncology",
        "freetext": (
            "65-year-old man with EGFR-mutant non-small cell lung cancer. "
            "No prior TKI therapy. ECOG 0."
        ),
    },
    "Colorectal Cancer": {
        "age": 52,
        "gender": "Female",
        "conditions": ["Colorectal Cancer"],
        "biomarkers": "",
        "prior_therapies": "oxaliplatin, fluorouracil",
        "ecog": "1",
        "area": "Oncology",
        "freetext": (
            "52-year-old woman with metastatic colorectal cancer. "
            "Prior FOLFOX (oxaliplatin + fluorouracil). ECOG 1."
        ),
    },
}

# ---------------------------------------------------------------------------
# Session state initialisation
# ---------------------------------------------------------------------------

_DEFAULTS: dict[str, object] = {
    "s_age":            58,
    "s_gender":         "Female",
    "s_conditions":     [],
    "s_biomarkers":     "",
    "s_prior_therapies": "",
    "s_ecog":           "Unknown",
    "s_area":           "All",
    "s_freetext":       "",
    "s_input_mode":     "Structured Input",
    "results":          None,
    "parsed_profile":   None,
}

for _k, _v in _DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ---------------------------------------------------------------------------
# Demo callbacks (on_click — run before next render)
# ---------------------------------------------------------------------------

def _load_demo(name: str) -> None:
    d = DEMOS[name]
    st.session_state.s_age = d["age"]
    st.session_state.s_gender = d["gender"]
    st.session_state.s_conditions = d["conditions"]
    st.session_state.s_biomarkers = d["biomarkers"]
    st.session_state.s_prior_therapies = d["prior_therapies"]
    st.session_state.s_ecog = d["ecog"]
    st.session_state.s_area = d["area"]
    st.session_state.s_freetext = d["freetext"]
    st.session_state.s_input_mode = "Structured Input"
    st.session_state.results = None
    st.session_state.parsed_profile = None

# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _call_match(payload: dict, top_n: int, explanations: bool) -> dict | None:
    try:
        resp = requests.post(
            f"{API_URL}/match",
            json=payload,
            params={"top_n": top_n, "include_explanations": explanations},
            timeout=90,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        st.error(f"Cannot connect to API at **{API_URL}**. Is the server running?")
    except requests.exceptions.Timeout:
        st.error("Request timed out (90 s). Try with fewer results or disable explanations.")
    except requests.exceptions.HTTPError as e:
        st.error(f"API error {e.response.status_code}: {e.response.text[:300]}")
    except Exception as exc:
        st.error(f"Unexpected error: {exc}")
    return None


def _call_parse(text: str) -> dict | None:
    try:
        resp = requests.post(
            f"{API_URL}/patient/parse",
            json={"text": text},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        st.error(f"Cannot connect to API at **{API_URL}**.")
    except Exception as exc:
        st.error(f"Parse error: {exc}")
    return None


def _build_payload(
    age: int | None,
    gender: str,
    conditions: list[str],
    biomarkers_text: str,
    prior_therapies_text: str,
    ecog: str,
    area: str,
) -> dict:
    """Map form inputs → PatientProfile JSON body."""
    # Biomarkers: "HER2=positive, EGFR=negative"
    biomarkers = []
    for entry in biomarkers_text.split(","):
        entry = entry.strip()
        if "=" in entry:
            name, status = entry.split("=", 1)
            biomarkers.append({"name": name.strip(), "status": status.strip().lower()})

    # Prior therapies → RxNorm CUIs
    therapy_names = [t.strip() for t in prior_therapies_text.split(",") if t.strip()]
    rxnorm_ids = [
        DRUG_TO_RXNORM[name.lower()]
        for name in therapy_names
        if name.lower() in DRUG_TO_RXNORM
    ]

    # Conditions → SNOMED IDs
    snomed_ids = [CONDITION_TO_SNOMED[c] for c in conditions if c in CONDITION_TO_SNOMED]

    ecog_int = int(ecog) if ecog not in ("Unknown", "") else None
    ta = area.lower().replace(" ", "_") if area != "All" else None

    return {
        "age": age,
        "gender": gender if gender != "Unknown" else None,
        "conditions": snomed_ids,
        "condition_names": conditions,
        "biomarkers": biomarkers,
        "prior_therapies": rxnorm_ids,
        "prior_therapy_names": therapy_names,
        "ecog_status": ecog_int,
        "therapeutic_area": ta,
    }

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

st.title("🧬 Clinical Trial Eligibility Matcher")
st.caption(
    "Powered by **Neo4j** knowledge graph · **SNOMED/RxNorm** entity linking · **LLM** scoring  "
    f"&nbsp;&nbsp;API: `{API_URL}`"
)

# ---------------------------------------------------------------------------
# Demo buttons
# ---------------------------------------------------------------------------

st.markdown("**Quick demos:**")
d_col1, d_col2, d_col3 = st.columns(3)
for col, demo_name in zip(
    [d_col1, d_col2, d_col3],
    list(DEMOS.keys()),
):
    col.button(
        demo_name,
        on_click=_load_demo,
        args=(demo_name,),
        use_container_width=True,
    )

st.divider()

# ---------------------------------------------------------------------------
# Sidebar — patient profile input
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("Patient Profile")

    area = st.selectbox(
        "Therapeutic Area",
        AREAS,
        index=AREAS.index(st.session_state.s_area)
        if st.session_state.s_area in AREAS else 0,
    )

    input_mode = st.radio(
        "Input Mode",
        ["Structured Input", "Free Text"],
        index=["Structured Input", "Free Text"].index(st.session_state.s_input_mode),
        horizontal=True,
    )

    st.divider()

    # ---- Structured Input ------------------------------------------------
    if input_mode == "Structured Input":
        age = st.number_input(
            "Age",
            min_value=0, max_value=120, step=1,
            value=int(st.session_state.s_age),
        )
        gender = st.selectbox(
            "Gender",
            GENDERS,
            index=GENDERS.index(st.session_state.s_gender)
            if st.session_state.s_gender in GENDERS else 0,
        )

        condition_opts = CONDITIONS_BY_AREA.get(area, CONDITIONS_BY_AREA["All"])
        valid_defaults = [c for c in st.session_state.s_conditions if c in condition_opts]
        conditions = st.multiselect("Conditions", condition_opts, default=valid_defaults)

        biomarkers_text = st.text_input(
            "Biomarkers",
            value=st.session_state.s_biomarkers,
            placeholder="e.g. HER2=positive, EGFR=negative",
            help="NAME=positive or NAME=negative, comma-separated",
        )
        prior_therapies_text = st.text_input(
            "Prior Therapies",
            value=st.session_state.s_prior_therapies,
            placeholder="e.g. trastuzumab, bevacizumab",
            help="Drug names, comma-separated",
        )
        ecog = st.selectbox(
            "ECOG Status",
            ECOG_OPTS,
            index=ECOG_OPTS.index(st.session_state.s_ecog)
            if st.session_state.s_ecog in ECOG_OPTS else 0,
        )

        # Keep session state in sync for demo reload to work correctly
        st.session_state.s_age = age
        st.session_state.s_gender = gender
        st.session_state.s_conditions = conditions
        st.session_state.s_biomarkers = biomarkers_text
        st.session_state.s_prior_therapies = prior_therapies_text
        st.session_state.s_ecog = ecog
        st.session_state.s_area = area
        st.session_state.s_input_mode = input_mode

        search_payload: dict | None = _build_payload(
            age, gender, conditions, biomarkers_text, prior_therapies_text, ecog, area
        )

    # ---- Free Text -------------------------------------------------------
    else:
        freetext = st.text_area(
            "Patient Description",
            value=st.session_state.s_freetext or (
                "58-year-old woman with HER2-positive metastatic breast cancer, "
                "previously treated with trastuzumab, ECOG performance status 1."
            ),
            height=130,
        )

        st.session_state.s_freetext = freetext
        st.session_state.s_input_mode = input_mode
        st.session_state.s_area = area

        if st.button("Extract Profile", use_container_width=True):
            with st.spinner("Parsing description…"):
                parsed = _call_parse(freetext)
            if parsed:
                st.session_state.parsed_profile = parsed
                st.success("Profile extracted!")

        if st.session_state.parsed_profile:
            st.markdown("**Extracted Profile:**")
            render_patient_summary(st.session_state.parsed_profile)
            _p = dict(st.session_state.parsed_profile)
            if area != "All":
                _p["therapeutic_area"] = area.lower().replace(" ", "_")
            search_payload = _p
        else:
            search_payload = None

    st.divider()

    top_n = st.slider("Max Results", 1, 20, 10)
    include_explanations = st.checkbox("Include LLM Explanations", value=True)

    find_clicked = st.button(
        "🔍 Find Matching Trials",
        type="primary",
        use_container_width=True,
    )

# ---------------------------------------------------------------------------
# Trigger search
# ---------------------------------------------------------------------------

if find_clicked:
    if search_payload is None:
        st.warning("Please extract a patient profile first (Free Text mode).")
    elif not search_payload.get("conditions") and not search_payload.get("condition_names"):
        st.warning("Please select at least one condition.")
    else:
        spinner_msg = (
            "Searching trials and generating LLM explanations…"
            if include_explanations
            else "Searching trials…"
        )
        with st.spinner(spinner_msg):
            st.session_state.results = _call_match(search_payload, top_n, include_explanations)

# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

results = st.session_state.results

if results:
    matches = results.get("matches", [])
    n = len(matches)
    total_c = results.get("total_candidates", n)
    total_e = results.get("total_after_exclusions", n)
    q_ms = results.get("query_time_ms", 0)
    patient_summary = results.get("patient_summary", "")
    ta_label = results.get("therapeutic_area") or "all areas"

    # Summary bar
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Candidates Found", total_c)
    m2.metric("After Exclusions", total_e)
    m3.metric("Showing", n)
    m4.metric("Query Time", f"{q_ms:.0f} ms")

    if patient_summary:
        st.caption(f"Patient: {patient_summary}  ·  Area: {ta_label}")

    st.markdown("---")

    if matches:
        for idx, match in enumerate(matches):
            render_trial_card(match, idx)
    else:
        st.info(
            "No matching trials found for this patient profile. "
            "Try broadening the conditions or removing the therapeutic area filter."
        )

elif not find_clicked:
    # Landing state — instructions
    st.markdown(
        """
        ### How to use

        1. Click a **Quick Demo** above to pre-fill a patient profile, or
        2. Build a custom profile in the **sidebar** using structured fields or free text
        3. Click **Find Matching Trials**

        The matcher will:
        - Traverse the **SNOMED IS_A hierarchy** to find candidate trials
        - Apply hard exclusion filters (age, gender, prior therapy conflicts)
        - **Score and rank** trials across 5 dimensions (condition, biomarker, therapy, demographics, quality)
        - Generate **LLM explanations** (✓/✗/? per criterion) for each match
        """
    )

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

st.markdown("---")
st.markdown(
    '<p style="text-align:center; color:#999; font-size:0.8em;">'
    "For research and educational purposes only. Not medical advice. "
    "Always consult a qualified healthcare professional.&nbsp;&nbsp;|&nbsp;&nbsp;"
    '<a href="https://clinicaltrials.gov" target="_blank">ClinicalTrials.gov</a>'
    "</p>",
    unsafe_allow_html=True,
)
