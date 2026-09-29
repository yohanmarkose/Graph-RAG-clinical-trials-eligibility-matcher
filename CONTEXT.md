# Clinical Trials Eligibility Matcher

A graph-first (not RAG) system that matches patients against ClinicalTrials.gov studies using a Neo4j graph of trials, eligibility criteria, and SNOMED/RxNorm ontology, with an LLM used for free-text parsing, multi-hop reasoning (ReKnoS), and generating match explanations.

## Language

**Physician**:
An authenticated user of the application who creates and manages their own Patients.
_Avoid_: User, Doctor, Clinician.

**Patient**:
A saved, reusable clinical profile (age, gender, conditions, biomarkers, prior therapies, ECOG status) created and owned by exactly one Physician, editable across sessions. Distinct from the one-off free-text/structured input previously entered per match.
_Avoid_: Patient Profile, Applicant.

**Match Run**:
A historical record of one eligibility-matching execution against a Patient, capturing the timestamp and a snapshot of the top trial results (trial ID, title, score, key reasons) at that time — a lightweight log, not a full audit trail. Stores a snapshot rather than a live reference, so history stays meaningful even after a trial is later excluded from the graph.
_Avoid_: Match History, Search.

**Match Explanation**:
The evidence shown to a Physician for why a Trial matched: an LLM-generated natural-language summary paired with the concrete list of matched/excluded criteria and the graph path that produced the score.
_Avoid_: Explanation (alone), Justification.
