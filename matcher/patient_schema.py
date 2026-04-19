from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class BiomarkerStatus(BaseModel):
    """A single biomarker and its expression status for a patient."""

    name: str
    """Biomarker name as it appears in the graph, e.g. "HER2", "EGFR", "PD-L1"."""

    status: str
    """Expression status: "positive" or "negative"."""


class LabValue(BaseModel):
    """A single laboratory result for a patient."""

    name: str
    """Lab test name, e.g. "ECOG", "Hemoglobin", "Creatinine"."""

    value: float
    """Numeric result."""

    unit: Optional[str] = None
    """Unit of measurement, e.g. "g/dL", "mg/dL". None when dimensionless."""


class PatientProfile(BaseModel):
    """Complete patient profile used as input to the matching engine.

    Conditions and prior therapies are stored as ontology concept IDs
    so they can be traversed through the SNOMED/RxNorm ontology backbone
    in Neo4j. The parallel *_names lists hold human-readable labels for
    display purposes only.
    """

    age: Optional[int] = None
    """Patient age in years."""

    gender: Optional[str] = None
    """Patient gender: "Male", "Female", or "All"."""

    conditions: list[str] = Field(default_factory=list)
    """SNOMED concept IDs for the patient's diagnoses, e.g. ["254837009"]."""

    condition_names: list[str] = Field(default_factory=list)
    """Free-text condition names (display only), parallel to `conditions`."""

    biomarkers: list[BiomarkerStatus] = Field(default_factory=list)
    """Biomarker expression statuses relevant to trial eligibility."""

    prior_therapies: list[str] = Field(default_factory=list)
    """RxNorm CUIs for drugs the patient has previously received."""

    prior_therapy_names: list[str] = Field(default_factory=list)
    """Free-text drug names (display only), parallel to `prior_therapies`."""

    lab_values: list[LabValue] = Field(default_factory=list)
    """Laboratory results, including ECOG performance status if available."""

    ecog_status: Optional[int] = None
    """ECOG performance status (0–5). Extracted from lab_values if present."""

    therapeutic_area: Optional[str] = None
    """Optional therapeutic area filter, e.g. "oncology". Narrows candidate set."""

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    def biomarker_map(self) -> dict[str, str]:
        """Return {normalised_name: status} for O(1) lookup during scoring."""
        return {b.name.lower(): b.status.lower() for b in self.biomarkers}

    def lab_map(self) -> dict[str, LabValue]:
        """Return {normalised_name: LabValue} for O(1) lookup during scoring."""
        return {lv.name.lower(): lv for lv in self.lab_values}
