import asyncio
from config.settings import settings
from llm.explainer import MatchExplainer
from llm.provider import get_llm_provider
from matcher.match_engine import MatchEngine
from matcher.patient_schema import BiomarkerStatus, PatientProfile
from neo4j import AsyncGraphDatabase


async def test():
    driver = AsyncGraphDatabase.driver(
        settings.neo4j.uri, auth=(settings.neo4j.user, settings.neo4j.password)
    )
    llm = get_llm_provider(settings)
    engine = MatchEngine(driver)
    explainer = MatchExplainer(llm)

    patient = PatientProfile(
        age=58,
        gender="Female",
        conditions=["427685000"],
        condition_names=["HER2-positive breast cancer"],
        biomarkers=[BiomarkerStatus(name="HER2", status="positive")],
        prior_therapies=["224905"],
        prior_therapy_names=["trastuzumab"],
        therapeutic_area="oncology",
    )

    matches = await engine.match(patient, top_n=3)
    print(f"Found {len(matches)} matches")

    if matches:
        explained = await explainer.explain_matches(patient, matches[:1])
        print("\n--- Explanation ---")
        print(explained[0]["explanation"])

    await driver.close()


asyncio.run(test())
