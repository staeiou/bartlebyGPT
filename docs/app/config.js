export const FEEDBACK_PATH = "/v1/feedback";
export const SETTINGS_KEY = "bartleby-remote-settings-v1";
export const TURN_COUNT_KEY = "bartleby-remote-turn-count-v1";
export const DEFAULT_BASE_URL = "https://api.bartlebygpt.org";
export const ECO_BASE_URL = "";
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
        powerProfile: "auto-live",
        wattsIdle: 120,
        wattsActive: 260,
        gco2PerWh: 0.200,
        costPerHr: 0.09,
        costPerKwh: 0.55,
        colorWarmth: 0.5,
      };

const LEGACY_ECO_ORIN_LABEL = "Eco (Jetson Orin Nano Super 8GB)";
const LEGACY_ECO_ORIN_MODAL_HTML = [
  `<p>BartlebyGPT is a satire, but the cost data is genuine.</p>`,
  `<p>These figures reflect the live power consumption of BartlebyGPT's eco deployment, which runs in a residential home in San Diego, CA on a Jetson Orin Nano Super 8GB. The machine pulls roughly <strong>~6–7W</strong> at idle and scales up to about <strong>~21W</strong> at full load while serving a low-power inference stack.</p>`,
  `<p><strong>Watts</strong> is the estimated total draw for all current BartlebyGPT users combined. For this deployment, we use live power telemetry from an Anker Solix C300X DC battery via BLE, so reported watts reflect total system draw directly. A small air conditioner window unit or TV with sound system pulls 400-600W, a fan pulls 50-75W, a MacBook Air streaming video pulls 20-30W, and an LED/CFL lightbulb pulls 10-15W.</p>`,
  `<p><strong>gCO₂/hr</strong> is the estimated carbon cost of that total estimated power draw: watts × grid carbon intensity. San Diego is served by <a href="https://sdcommunitypower.org/wp-content/uploads/2025/11/Community-Power-2024-Power-Content-Label.pdf">San Diego Community Power</a> cleaner energy program via SDG&amp;E, which has a ~53% renewables mix. This has an estimated emissions factor of ~0.200 gCO₂/Wh, though this varies with the grid mix and time of day. This does not include emissions from training and fine-tuning the model, manufacturing the hardware, disposing of end-of-life hardware, networking between your device and the server, or other inputs and externalities.</p>`,
  `<p><strong>$/hr</strong> is the estimated electricity cost accrued continuously whether idle or busy, shared across all users of the site. It is computed in real time from SDG&amp;E TOU-DR2 all-in variable energy rates (on/off-peak) and excludes fixed charges, taxes, and baseline credits.</p>`,
  `<p>All parameters are adjustable under <em>Advanced</em>.</p>`,
].join("");

export const POWER_PROFILES = {
        "auto-live": {
          label: "Auto (Live Telemetry)",
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
            `<p>This mode automatically selects the most appropriate deployment profile for the active backend and uses live telemetry fields whenever available.</p>`,
            `<p>On the main API deployment, Auto resolves to the base Jetson profile. On the Pi deployment, Auto resolves to the Raspberry Pi profile. The home RTX profile remains available as a manual selection.</p>`,
            `<p>All parameters are adjustable under <em>Advanced</em>.</p>`,
          ].join(""),
        },
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
            `<p><strong>Watts</strong> is the estimated total draw for all current BartlebyGPT users combined. When idle, we use the configured baseline (~120W). When requests are running, we take that baseline and add the GPU's live measured wattage. This generates a small amount of heat, but San Diego has a mild climate. A small air conditioner window unit or TV with sound system pulls 400-600W, a fan pulls 50-75W, a MacBook Air streaming video pulls 20-30W, and an LED/CFL lightbulb pulls 10-15W.</p>`,
            `<p><strong>gCO₂/hr</strong> is the estimated carbon cost of that total estimated power draw: watts × grid carbon intensity. San Diego is served by <a href="https://sdcommunitypower.org/wp-content/uploads/2025/11/Community-Power-2024-Power-Content-Label.pdf">San Diego Community Power</a> cleaner energy program via SDG&amp;E, which has a ~53% renewables mix. This has an estimated emissions factor of ~0.200 gCO₂/Wh, though this varies with the grid mix and time of day. This does not include emissions from training and fine-tuning the model, manufacturing the hardware, disposing of end-of-life hardware, networking between your device and the server, or other inputs and externalities.</p>`,
            `<p><strong>$/hr</strong> is the estimated electricity cost accrued continuously whether idle or busy, shared across all users of the site. It is computed in real time from SDG&amp;E TOU-DR2 all-in variable energy rates (on/off-peak) and excludes fixed charges, taxes, and baseline credits.</p>`,
            `<p>All parameters are adjustable under <em>Advanced</em>.</p>`,
          ].join(""),
        },
        "eco-orin": {
          label: "Base (Jetson Orin Nano Super 8GB)",
          legacyLabel: LEGACY_ECO_ORIN_LABEL,
          costMode: "per-kwh",
          overheadMultiplier: 1.0,
          defaults: {
            wattsIdle: 7.5,
            wattsActive: 21,
            gco2PerWh: 0.00,
            costPerKwh: 0.00,
          },
          legacyModalHtml: LEGACY_ECO_ORIN_MODAL_HTML,
          modalHtml: [
            `<p>BartlebyGPT is a satire, but the infrastructure enacts a different critique. The power data is genuine, as it is running an open, sovereign, on-the-box AI with off-the-grid solar. The closed-loop system is running in San Diego on a ultra-low power edge computer powered by a solar battery setup, connected via 4G with Wi-Fi backup.</p>`,
            `<p><strong>Materials: </strong>A NVIDIA Jetson Orin Nano Super plugs into an Anker Solix C300X DC (288Wh capacity) powered by a 100W solar panel. The Jetson pulls roughly 8W at idle and up to ~21W under inference load, which can generate about 20 simultaneous replies.</p>`,
            `<p><strong>Software: </strong> The LLM is <a href="https://huggingface.co/staeiou/bartleby-qwen3-1.7b_v4">an open-weight 1.7B model</a> I fine-tuned with Alibaba's <a href-"https://huggingface.co/Qwen/Qwen3-1.7B">Qwen3-1.7B</a> using <a href="https://huggingface.co/unsloth/Qwen3-1.7B-GGUF">Unsloth</a>, served via <a href="https://github.com/vllm-project/vllm">vLLM</a>.</p>`,
            `<p><strong>% battery </strong> is directly pulled from the C300X, live with a 1-2 second delay. The background color is also a battery meter: the lower the battery, the more of the background is grey. This UI idea and icons are shamelessly stolen from <a href="https://solar.lowtechmagazine.com/power">Low Tech Magazine</a>.</p>`,
            `<p><strong>Watts </strong> is the live total DC output measured from the C300X battery management system. A box or ceiling fan pulls 50-75W, a MacBook Air streaming video pulls 20-30W, and a standard LED/CFL room lightbulb pulls 10-15W.</p>`,
            `<p><strong>Solar </strong> is the live solar input to the battery, also read from the C300X. When solar input exceeds total draw, the battery is gaining charge and the server is running on pure sunlight. When it's dark or overcast, the Jetson is powered by the battery.</p>`,
            `<p>Because this deployment is solar-charged, its operational electricity cost and carbon footprint is approximately zero. This does not include the carbon cost in manufacturing and distributing the Jetson, battery, solar panel, your device, and network infrastructure.</p>`,
          ].join(""),
        },
        "pi-rpi4": {
          label: "Pi (Raspberry Pi 4B)",
          costMode: "per-kwh",
          overheadMultiplier: 1.0,
          defaults: {
            wattsIdle: 3,
            wattsActive: 12,
            gco2PerWh: 0.200,
            costPerKwh: 0.55,
          },
          modalHtml: [
            `<p>BartlebyGPT is a satire, but the power data is genuine. This pi.bartlebygpt.org site and the LLM behind it are running entirely on a single Raspberry Pi 5 on solar power and batteries.</p>`,
            `<p><strong>Materials:</strong>The Pi5 plugs into an Anker Solix C300X DC (288Wh capacity) powered by a 50W solar panel. The Pi pulls roughly 3–5W at idle and up to ~12W under inference load. It generates a small amount of heat, but far less than a night-light.</p>`,
            `<p><strong>% battery </strong> is directly pulled from the C300X. The background color is also a battery meter: the lower the battery, the more of the background is grey. This UI idea and icons are shamelessly stolen from <a href="https://solar.lowtechmagazine.com/power">Low Tech Magazine</a>.</p>`,
            `<p><strong>Watts </strong> is the live total DC output, measured every two seconds from the C300X's battery management system over Bluetooth. A box or ceiling fan pulls 50–75W, a MacBook Air streaming video pulls 20–30W, and a standard LED/CFL room lightbulb pulls 10–15W.</p>`,
            `<p><strong>Solar </strong> is the live solar input to the battery, also read directly from the C300X. When solar input exceeds total draw, the battery is gaining charge and the server is running on pure sunlight. When it's dark or overcast, the Pi is powered by the battery.</p>`,
            `<p>Because this deployment is solar-charged, its operational electricity cost and carbon footprint is approximately zero. This does not include the carbon cost in manufacturing and distributing the Pi, the battery, the solar panels, your device, and the network infrastructure from your device to the Pi and back.</p>`,
          ].join(""),
        },
        "jetson-solar-lfp": {
          label: "Solar LFP (Jetson Orin Nano Super 8GB)",
          costMode: "per-kwh",
          overheadMultiplier: 1.0,
          defaults: {
            wattsIdle: 7.5,
            wattsActive: 21,
            gco2PerWh: 0.00,
            costPerKwh: 0.00,
          },
          modalHtml: [
            `<p>BartlebyGPT is a satire, but the infrastructure enacts a different critique. The power data is genuine, as it is running an open, sovereign, on-the-box AI with off-the-grid solar. The closed-loop system is running in San Diego on a ultra-low power edge computer powered by a solar battery setup, connected via 4G with Wi-Fi backup.</p>`,
            `<p><strong>Materials: </strong>A NVIDIA Jetson Orin Nano Super plugs into a Victron SmartSolar MPPT 100/20 charge controller, which manages a 12V 100Ah LFP battery (1280Wh capacity) fed by a solar panel. The Jetson pulls roughly 8W at idle and up to ~21W under inference load.</p>`,
            `<p><strong>Software: </strong> The LLM is <a href="https://huggingface.co/staeiou/bartleby-qwen3-1.7b_v4">an open-weight 1.7B model</a> I fine-tuned with Alibaba's <a href="https://huggingface.co/Qwen/Qwen3-1.7B">Qwen3-1.7B</a> using <a href="https://huggingface.co/unsloth/Qwen3-1.7B-GGUF">Unsloth</a>, served via <a href="https://github.com/vllm-project/vllm">vLLM</a>.</p>`,
            `<p><strong>% battery </strong> is derived from the battery's remaining capacity in amp-hours, read live from the JBD BMS over BLE. The background color is also a battery meter: the lower the battery, the more of the background is grey. This UI idea and icons are shamelessly stolen from <a href="https://solar.lowtechmagazine.com/power">Low Tech Magazine</a>.</p>`,
            `<p><strong>Watts </strong> is the live load output measured from the Victron charge controller. A box or ceiling fan pulls 50-75W, a MacBook Air streaming video pulls 20-30W, and a standard LED/CFL room lightbulb pulls 10-15W.</p>`,
            `<p><strong>Solar </strong> is the live solar input from the Victron charge controller. When solar input exceeds total draw, the battery is gaining charge and the server is running on pure sunlight. When it's dark or overcast, the Jetson is powered by the battery.</p>`,
            `<p>Because this deployment is solar-charged, its operational electricity cost and carbon footprint is approximately zero. This does not include the carbon cost in manufacturing and distributing the Jetson, battery, solar panel, your device, and network infrastructure.</p>`,
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
            `<p><strong>Watts</strong> is the estimated total draw for all current BartlebyGPT users combined. We start with our 300W base 1/4th share of the server, then add the GPU's actual live measured wattage. The RTX A4000 directly pulls ~15W when idle and ~150W at full load, but also generates heat and network traffic. The 1/4th server + live 1 GPU load is multiplied by 1.35× to account for energy costs in cooling, networking, and other data center infrastructure overhead (<a href="https://en.wikipedia.org/wiki/Power_usage_effectiveness">PUE factor</a>). A small air conditioner window unit or TV with sound system pulls 400-600W, a fan pulls 50-75W, a MacBook Air streaming video pulls 20-30W, and an LED/CFL lightbulb pulls 10-15W.</p>`,
            `<p><strong>gCO₂/hr</strong> is the estimated carbon cost of that total estimated power draw: watts × grid carbon intensity. Avista Power serves Spokane with <a href="https://www.myavista.com/about-us/about-our-energy-mix">a ~60% renewable power mix</a>, giving an estimated <a href="https://www.epa.gov/energy/greenhouse-gas-equivalencies-calculator">emissions factor of ~0.3 gCO₂/Wh</a>, although this can be higher if the data center burns its own fossil fuel generators when grid capacity is low. This does not include emissions from hardware manufacturing, end-of-life disposal, data center workers' commutes, or private jets to lobby politicians about data center regulations.</p>`,
            `<p><strong>$/hr</strong> is our rental cost for this GPU and our quarter-server share, accrued continuously whether idle or busy, shared across all users of the site.</p>`,
            `<p>All parameters are adjustable under <em>Advanced</em>.</p>`,
          ].join(""),
        },
      };
