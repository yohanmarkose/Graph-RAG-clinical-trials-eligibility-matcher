# Patient data is not represented in the Neo4j graph

Neo4j models Trials, ontology concepts, and Criteria — reference data that's rebuilt wholesale from Snowflake/CSV sources. We decided saved Patients stay entirely in the new Postgres store and are never written into Neo4j (no `Patient` node, no `MATCHED_TO` edge), so the graph remains a stateless reference/matching engine queried fresh on every match rather than accumulating per-patient history. Cross-patient graph analytics would require revisiting this later.
