import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

export function ModeBanner() {
  const { data: meta, isError } = useQuery({ queryKey: ["meta"], queryFn: api.meta, refetchInterval: 15000 });
  // No banner must not look like live mode: an unknown mode is said out loud.
  if (isError) return <s-banner tone="warning">Could not confirm which model and store this is using.</s-banner>;
  if (!meta) return null;
  const notes: string[] = [];
  if (meta.model_mode === "demo") notes.push("Demo model: fixes come from a template script, not an LLM.");
  if (meta.shopify_adapter === "fixture") notes.push("The store is simulated.");
  if (meta.demo_mode) notes.push("Demo sign-in is on.");
  if (meta.public_demo) {
    notes.push(`This is a shared sandbox: other visitors see what you do, and it resets every ${meta.demo_reset_minutes} minutes.`);
  }
  if (!meta.writeback_shop_enabled || !meta.writeback_env_enabled) notes.push("Writing to the store is switched off.");
  if (notes.length === 0) return null;
  return <s-banner tone="info">{notes.join(" ")}</s-banner>;
}
