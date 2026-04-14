# Test Patient Profiles

Copy-paste these into the Streamlit frontend to test the matcher.

---

## SHOULD MATCH WELL

### 1. HER2+ Breast Cancer (55F)

**Free Text:**
```
55-year-old woman with HER2-positive breast cancer. ECOG performance status 1. No prior systemic therapy.
```

**Structured Input:**
- Age: 55
- Gender: Female
- Therapeutic Area: Oncology
- Conditions: Breast Cancer
- Biomarkers: HER2=positive
- Prior Therapies: (leave empty)
- ECOG: 1

---

### 2. EGFR+ Non-Small Cell Lung Cancer (62M)

**Free Text:**
```
62-year-old male with EGFR-mutant non-small cell lung cancer. Treatment-naive. ECOG 0.
```

**Structured Input:**
- Age: 62
- Gender: Male
- Therapeutic Area: Oncology
- Conditions: Non-Small Cell Lung Cancer
- Biomarkers: EGFR=positive
- Prior Therapies: (leave empty)
- ECOG: 0

---

### 3. Acute Myeloid Leukemia with Prior Chemo (48M)

**Free Text:**
```
48-year-old man with relapsed acute myeloid leukemia. Previously treated with cyclophosphamide. ECOG 1.
```

**Structured Input:**
- Age: 48
- Gender: Male
- Therapeutic Area: Oncology
- Conditions: Leukemia
- Biomarkers: (leave empty)
- Prior Therapies: cyclophosphamide
- ECOG: 1

---

### 4. Multiple Myeloma (67M)

**Free Text:**
```
67-year-old man with newly diagnosed multiple myeloma. No prior therapy. ECOG performance status 1.
```

**Structured Input:**
- Age: 67
- Gender: Male
- Therapeutic Area: Oncology
- Conditions: Lymphoma
- Biomarkers: (leave empty)
- Prior Therapies: (leave empty)
- ECOG: 1

---

### 5. Hepatocellular Carcinoma + PD-L1+ (59M)

**Free Text:**
```
59-year-old man with hepatocellular carcinoma, PD-L1 positive. Previously treated with bevacizumab. ECOG 0.
```

**Structured Input:**
- Age: 59
- Gender: Male
- Therapeutic Area: Oncology
- Conditions: Pancreatic Cancer (closest available in dropdown)
- Biomarkers: PD-L1=positive
- Prior Therapies: bevacizumab
- ECOG: 0

---

## SHOULD PARTIALLY MATCH

### 6. Elderly Breast Cancer Patient (89F)

**Free Text:**
```
89-year-old woman with breast cancer. Poor performance status, ECOG 3. No prior therapy.
```

**Structured Input:**
- Age: 89
- Gender: Female
- Therapeutic Area: Oncology
- Conditions: Breast Cancer
- Biomarkers: (leave empty)
- Prior Therapies: (leave empty)
- ECOG: 3

**Why partial:** Age 89 and ECOG 3 will fail the exclusion criteria on many trials. Fewer results and lower scores compared to Patient 1.

---

### 7. Generic Cancer (50F)

**Free Text:**
```
50-year-old woman diagnosed with cancer. Specific subtype pending pathology.
```

**Structured Input:**
- Age: 50
- Gender: Female
- Therapeutic Area: Oncology
- Conditions: (select any broad cancer)
- Biomarkers: (leave empty)
- Prior Therapies: (leave empty)
- ECOG: Unknown

**Why partial:** Very broad condition, no biomarkers or therapies. Matches will be generic with lower specificity scores.

---

## SHOULD FAIL / NO MATCHES

### 8. Heart Failure Patient (70M) - Wrong Area

**Free Text:**
```
70-year-old man with congestive heart failure. NYHA class III. On metoprolol and lisinopril.
```

**Structured Input:**
- Age: 70
- Gender: Male
- Therapeutic Area: Cardiology
- Conditions: Heart Failure
- Biomarkers: (leave empty)
- Prior Therapies: (leave empty)
- ECOG: Unknown

**Why no match:** The graph only has oncology trials. No cardiology conditions have SNOMED links to any trial criteria.

---

### 9. No Conditions

**Free Text:**
```
40-year-old healthy woman seeking preventive screening.
```

**Structured Input:**
- Age: 40
- Gender: Female
- Therapeutic Area: All
- Conditions: (none selected)
- Biomarkers: (leave empty)
- Prior Therapies: (leave empty)
- ECOG: Unknown

**Why no match:** The matching engine requires at least one condition to find candidate trials.

---

### 10. Pediatric AML Patient (8M)

**Free Text:**
```
8-year-old boy with acute myeloid leukemia. No prior treatment. ECOG 0.
```

**Structured Input:**
- Age: 8
- Gender: Male
- Therapeutic Area: Oncology
- Conditions: Leukemia
- Biomarkers: (leave empty)
- Prior Therapies: (leave empty)
- ECOG: 0

**Why no/few matches:** Most oncology trials require age >= 18. The age exclusion filter will remove nearly all candidates.
