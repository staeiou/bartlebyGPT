export const SETTINGS_KEY = "bartleby-remote-settings-v1";
export const TURN_COUNT_KEY = "bartleby-remote-turn-count-v1";
export const DEFAULT_BASE_URL = "https://api.bartlebygpt.org";
export const MODEL_MAX_CONTEXT_TOKENS = 2048;
export const CHARS_PER_TOKEN_ESTIMATE = 2.6;
export const TOKEN_SAFETY_MARGIN = 32;
export const defaults = {
        baseUrl: "",
        modelName: "",
        apiKey: "",
        systemPrompt: "",
        maxInputChars: 2000,
        maxNewTokens: 512,
        requestTimeout: 600,
        temperature: 0.7,
        topP: 0.9,
        powerProfile: "home-sd",
        wattsIdle: 120,
        wattsActive: 260,
        gco2PerWh: 0.200,
        costPerHr: 0.09,
        costPerKwh: 0.55,
      };

export const POWER_PROFILES = {
        "home-sd": {
          label: "Home (San Diego, Quadro RTX 4000)",
          costMode: "per-kwh",
          overheadMultiplier: 1.0,
          defaults: {
            wattsIdle: 120,
            wattsActive: 260,
            gco2PerWh: 0.200,
            costPerKwh: 0.55,
          },
          modalHtml: [
            `<p>BartlebyGPT is a satire, but the cost data is genuine.</p>`,
            `<p>These figures reflect the live power consumption of BartlebyGPT's server, which runs in a residential home in San Diego, CA on one GPU (Quadro RTX 4000). The machine pulls ~120W at idle and up to ~300W when under full GPU load. This single GPU can generate around 4–6 replies in parallel at a time.</p>`,
            `<p><strong>Watts</strong> is the estimated total draw for all current BartlebyGPT users combined. When idle, we use the configured baseline (~120W). When requests are running, we take that baseline and add the GPU's live measured wattage. This generates a small amount of heat, but San Diego has a mild climate. For reference, a small space heater, air conditioner window unit, or large TV and sound system pulls ~500–1000W.</p>`,
            `<p><strong>gCO₂/hr</strong> is the estimated carbon cost of that total estimated power draw: watts × grid carbon intensity. San Diego is served by <a href="https://sdcommunitypower.org/wp-content/uploads/2025/11/Community-Power-2024-Power-Content-Label.pdf">San Diego Community Power</a> cleaner energy program via SDG&amp;E, which has a ~53% renewables mix. This has an estimated emissions factor of ~0.200 gCO₂/Wh, though this varies with the grid mix and time of day. This does not include emissions from training and fine-tuning the model, manufacturing the hardware, disposing of end-of-life hardware, networking between your device and the server, or other inputs and externalities.</p>`,
            `<p><strong>$/hr</strong> is the estimated electricity cost accrued continuously whether idle or busy, shared across all users of the site. It is computed as (Watts ÷ 1000) × the configured electricity rate, based on SDG&amp;E residential rates.</p>`,
            `<p>All parameters are adjustable under <em>Advanced</em>.</p>`,
          ].join(""),
        },
        "spokane-dc": {
          label: "TierPoint (Spokane, WA data center)",
          costMode: "fixed-per-hr",
          overheadMultiplier: 1.35,
          defaults: {
            wattsIdle: 300,
            wattsActive: 450,
            gco2PerWh: 0.30,
            costPerHr: 0.09,
          },
          modalHtml: [
            `<p>BartlebyGPT is a satire, but the cost data is genuine.</p>`,
            `<p>These figures reflect the live power consumption of BartlebyGPT's server, which runs on one GPU (RTX A4000) in a 4-GPU server at <a href="https://www.tierpoint.com/data-centers/washington/spokane/">TierPoint's Spokane, WA data center</a>. The server directly pulls ~1.2 Kilowatts (KW) when idle and ~2.2KW when at full load, but we are only renting 1/4th of it. This single GPU and 1/4th of a server can generate around 15 replies in parallel at a time.</p>`,
            `<p><strong>Watts</strong> is the estimated total draw for all current BartlebyGPT users combined. We start with our 300W base 1/4th share of the server, then add the GPU's actual live measured wattage. The RTX A4000 directly pulls ~15W when idle and ~150W at full load, but also generates heat and network traffic. The 1/4th server + live 1 GPU load is multiplied by 1.35× to account for energy costs in cooling, networking, and other data center infrastructure overhead (<a href="https://en.wikipedia.org/wiki/Power_usage_effectiveness">PUE factor</a>). For reference, a small space heater, air conditioner window unit, or large TV and sound system pulls ~500-1000W.</p>`,
            `<p><strong>gCO₂/hr</strong> is the estimated carbon cost of that total estimated power draw: watts × grid carbon intensity. Avista Power serves Spokane with <a href="https://www.myavista.com/about-us/about-our-energy-mix">a ~60% renewable power mix</a>, giving an estimated <a href="https://www.epa.gov/energy/greenhouse-gas-equivalencies-calculator">emissions factor of ~0.3 gCO₂/Wh</a>, although this can be higher if the data center burns its own fossil fuel generators when grid capacity is low. This does not include emissions from hardware manufacturing, end-of-life disposal, data center workers' commutes, or private jets to lobby politicians about data center regulations.</p>`,
            `<p><strong>$/hr</strong> is our rental cost for this GPU and our quarter-server share, accrued continuously whether idle or busy, shared across all users of the site.</p>`,
            `<p>All parameters are adjustable under <em>Advanced</em>.</p>`,
          ].join(""),
        },
      };
