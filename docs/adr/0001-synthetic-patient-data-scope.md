# Patient data is synthetic/demo only, not real PHI

Adding physician login and saved patient profiles could plausibly be built to handle real patient health information, which would require HIPAA-grade controls (encryption at rest, audit logging, BAA-eligible hosting). We decided to keep this a portfolio/demo project: physicians enter synthetic or de-identified patient data, and the system makes no real-PHI compliance guarantees. This keeps auth, storage, and hosting choices simple; revisiting it for real clinical use would require a substantial security/compliance pass later.
