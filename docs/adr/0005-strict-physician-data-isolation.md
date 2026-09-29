# Strict per-physician data isolation, no shared or admin view

Physicians save Patients they add; we decided each Physician can only ever see and manage their own Patients, with no shared roster, care-team visibility, or admin override in this build. This is the simplest model and matches the feature as scoped, but it's a deliberate no: multi-provider collaboration on a shared patient (common in real clinical workflows) is out of scope until a permissions model is explicitly designed for it.
