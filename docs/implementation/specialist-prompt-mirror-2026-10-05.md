# Approved specialist prompt mirror, 2026-10-05

After automatic approval review rejected export without artifact-specific permission, the user explicitly approved uploading the nine generic templates in `specialists-dev-v1` to their configured Langfuse US project. The exact release was synchronized from the local hash-verified registry; no customer records, keys or traces were included in this upload.

```bash
docker compose --env-file .env -f infra/compose/compose.yaml exec -T api \
  python scripts/sync_prompts_to_langfuse.py specialists-dev-v1
docker compose --env-file .env -f infra/compose/compose.yaml exec -T api \
  python scripts/sync_prompts_to_langfuse.py specialists-dev-v1 --check
```

Both commands returned `status=ok`, mapping **9/9 prompts**, each to Cloud version1, with **zero drift**. Readback checked exact local template/hash matches and `resolveai-release` labels. The map remains in the ignored Docker observability volume and was copied into ignored `observability/prompt_mirror_map.json` for local host runs.

The runtime reads this map for generation metadata when the release and status match; it always loads actual prompt text from the local committed release. No paid model call was needed to verify the mirror. This resolves the artifact upload block; larger locked-run reconciliation and authoritative Cloud billing/project-tier confirmation remain open.
