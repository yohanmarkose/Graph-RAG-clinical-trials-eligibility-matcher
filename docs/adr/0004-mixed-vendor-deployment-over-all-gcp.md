# Deploy on a mixed-vendor stack rather than all-GCP

We considered deploying entirely on GCP (Cloud Run, Firebase Hosting, Cloud SQL, Firebase Auth) for simplicity of a single vendor. We decided against it: Cloud SQL bills continuously even when idle (~$7-10/month minimum) and Firebase Hosting needs more manual CDN/cache setup than git-push static hosts. Instead we're using Cloud Run for the FastAPI backend (still the strongest fit here), paired with Cloudflare Pages or Vercel for the React frontend, Neon or Supabase for Postgres, and Neo4j AuraDB for the graph — each picked as the cheapest/lowest-ops option for its piece rather than defaulting to GCP for all of them. Neo4j AuraDB's free-tier node/relationship limits need direct verification against our actual graph size before relying on the free tier.

**Considered options**: all-GCP (rejected — costs more at idle, more manual ops for the same result).
