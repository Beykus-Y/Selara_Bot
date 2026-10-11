# CI service images

The three backend service declarations in `.github/workflows/ci.yml` pull
PostgreSQL 16 and Redis 7 directly from Google's Docker Hub cache,
`mirror.gcr.io/library`, pinned to verified multi-platform image digests.
This avoids unauthenticated Docker Hub service pulls before workflow steps run.
The PostgreSQL/Redis ports, environment and healthchecks are unchanged.

Google documents the cache, its upstream synchronization and direct pulls at:
https://cloud.google.com/artifact-registry/docs/pull-cached-dockerhub-images

On 2026-10-10 the mirror and Docker Hub manifest lists were identical for
both images, and direct mirror pulls ran PostgreSQL 16.15 and Redis 7.4.11.

To update an image, compare the official Docker Hub and mirror manifests,
pull the mirror image and verify its runtime major version and healthcheck.
Update all three declarations together and require the complete CI run.
Do not substitute a different publisher or bypass checksum/TLS validation.

The public cache does not guarantee indefinite retention or availability.
Direct image URLs have no automatic Docker Hub fallback: an unavailable
pinned mirror image fails setup visibly rather than silently reintroducing
Docker Hub rate limits. Validate a replacement digest through a separate PR.
The production image Dockerfiles still use their existing upstream bases;
this change addresses CI service initialization, not production publishing.
