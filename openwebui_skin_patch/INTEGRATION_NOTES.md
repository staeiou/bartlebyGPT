# Integration Notes (Copy Into llama.cpp webui)

Target project:

- `/home/ubuntu/vllm_jetson/llama.cpp/tools/server/webui`

Source patch workspace:

- `/home/ubuntu/vllm_jetson/bartlebyGPT/openwebui_skin_patch/new-webui/src/lib/...`

## Files To Copy

1. `src/lib/constants/bartleby-prompts.ts`
2. `src/lib/constants/bartleby-power-profiles.ts`
3. `src/lib/stores/bartleby-power.svelte.ts`
4. `src/lib/components/app/bartleby/WelcomeBartleby.svelte`
5. `src/lib/components/app/bartleby/BartlebyPowerBar.svelte`
6. `src/lib/components/app/bartleby/DialogPowerInfo.svelte`
7. `src/lib/components/app/bartleby/index.ts`

## Integration Point A: Empty State Welcome

File:

- `src/lib/components/app/chat/ChatScreen/ChatScreen.svelte`

Action:

1. Import `WelcomeBartleby`.
2. In the empty-state branch, replace the current placeholder panel with `WelcomeBartleby`.
3. Wire `onPromptSelect` to draft/send path:
   - set initial message
   - focus chat form

## Integration Point B: Power Bar In Header

File:

- `src/lib/components/app/chat/ChatScreen/ChatScreenHeader.svelte`

Action:

1. Import `BartlebyPowerBar` and `DialogPowerInfo`.
2. Create metrics state by:
   - polling `/telemetry/power`
   - using helpers from `bartleby-power.svelte.ts`
3. Render bar alongside settings button.
4. Open/close modal on bar/info click.

## Integration Point C: Settings Fields

Files:

- `src/lib/constants/settings-config.ts`
- `src/lib/constants/settings-keys.ts`
- `src/lib/components/app/chat/ChatSettings/ChatSettings.svelte`

Action:

1. Add Bartleby power config keys:
   - powerProfile, wattsIdle, wattsActive, gco2PerWh, costPerHr, costPerKwh
2. Add UI fields in Settings sections.
3. Optional: add baseUrl/modelName parity later.

## Validation

```bash
cd /home/ubuntu/vllm_jetson/llama.cpp/tools/server/webui
npm run check
npm run build
```

