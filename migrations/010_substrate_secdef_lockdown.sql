-- CAI-RESP-304: lock anon-exec SECDEF governance funcs on the substrate (tscuymav) to service_role.
-- Quranic-ref reads (get_root_details, resolve_ayah_refs) are intentional public reads -> exempt (not revoked).
REVOKE EXECUTE ON FUNCTION public.enforce_challenge_window_timeouts(boolean) FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.sweep_stale_agents(interval, text, text) FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.get_decision(text) FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.get_repo_context(text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.enforce_challenge_window_timeouts(boolean) TO service_role;
GRANT EXECUTE ON FUNCTION public.sweep_stale_agents(interval, text, text) TO service_role;
GRANT EXECUTE ON FUNCTION public.get_decision(text) TO service_role;
GRANT EXECUTE ON FUNCTION public.get_repo_context(text) TO service_role;
