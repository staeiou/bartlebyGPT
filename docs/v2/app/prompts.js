export const ETHICS_PROMPT = {
  kicker: "Reason",
  title: "Resolve the Dilemma",
  description: "Hand over a real or hypothetical moral dilemma and get back the competing obligations laid out, the strongest case for each horn, where the genuine crux lies, and a defended resolution.",
  prompt: "Resolve the moral dilemma of [situation / choice / conflict], identifying the competing obligations at stake, the strongest case for each course of action, where the genuine crux lies, and a defended all-things-considered resolution: [paste the situation, constraints, or relevant details here]",
};

export const STARTER_POOL = [
  {
    kicker: "Expose",
    title: "Denaturalize the Ideology",
    description: "Hand over a text, policy, or cultural form and get back a critique of its ideological function: what it presents as natural, what it conceals, and what a demystified account reveals.",
    prompt: "Critique the ideological function of [text / policy / cultural form], identifying what it presents as natural or inevitable, what social relations it conceals, how it interpellates its subjects, and what a demystified account of its conditions of production reveals: [paste text, argument, or context here]",
  },
  {
    kicker: "Read",
    title: "Deconstruct the Discourse",
    description: "Hand over a text, argument, or discourse and get back a deconstructive reading: the binary oppositions it depends on, the exclusions they naturalize, and what its own contradictions reveal.",
    prompt: "Deconstruct [text / discourse], identifying the binary oppositions it depends on, showing how the privileged term is contaminated by what it excludes, tracing how these hierarchies naturalize particular material interests or power relations, and locating where the discourse undermines its own foundations: [paste text, argument, or context here]",
  },
  {
    kicker: "Expose",
    title: "Critique the Institution",
    description: "Hand over an institution, discourse, or practice and get back a critical analysis: key assumptions, who benefits, what harms and exclusions it produces, contradictions in its justifications, and what a genuine reckoning demands.",
    prompt: "Critique [institution / discourse], unpacking its key concepts and assumptions, identifying who benefits and who bears costs, surfacing the harms and exclusions it produces or legitimizes, highlighting contradictions in common justifications, and assessing what a genuine reckoning demands: [paste text, argument, or context here]",
  },
  {
    kicker: "Analyze",
    title: "Open the Black Box",
    description: "Hand over a technology or scientific claim and get back an STS-informed analysis: the social and political relations embedded in its design, whose knowledge counts, and what a symmetrical account reveals.",
    prompt: "Open the black box of [technology / scientific claim], tracing the social, material, and political relations embedded in its design, identifying whose knowledge counts and whose is excluded, and showing what a symmetrical account of its construction reveals: [paste text, description, or context here]",
  },
  {
    kicker: "Trace",
    title: "Historicize the Problem",
    description: "Hand over a concept, category, or practice and get back a genealogy: the contingent conditions of its emergence, the interests that shaped it, the alternatives it displaced, and what that history reveals about the present.",
    prompt: "Historicize [concept / practice], tracing the contingent conditions under which it emerged, the interests and conflicts that shaped it, the alternatives it displaced, and what its genealogy reveals about the present: [paste text, description, or context here]",
  },
  {
    kicker: "Analyze",
    title: "Map the Power",
    description: "Hand over an institution, practice, or discourse and get back a Foucauldian analysis: how subjects are produced, what knowledge authorizes its operations, where resistance emerges, and what its governmental logic reveals.",
    prompt: "Map the relations of power operating through [institution / discourse], identifying how subjects are produced, what forms of knowledge authorize its operations, where resistance emerges, and what its disciplinary or governmental logic makes visible: [paste text, description, or context here]",
  },
  {
    kicker: "Analyze",
    title: "Read the Economy",
    description: "Hand over an industry, policy, or market and get back a political-economic analysis: whose labor is extracted, what accumulation strategies it enables, what it externalizes, and whose interests it serves.",
    prompt: "Read the political economy of [industry / policy], identifying whose labor is extracted and whose is valued, what accumulation strategies it enables, what it externalizes, and what structural interests its arrangements serve: [paste text, description, or context here]",
  },
  {
    kicker: "Unsettle",
    title: "Trouble the Category",
    description: "Hand over a category, identity, or norm and get back a queer-theoretical analysis: how it is produced and policed, what performances sustain it, whose lives it excludes, and what its instability reveals.",
    prompt: "Trouble [category / norm], identifying how it is produced and policed, what performances sustain it, whose bodies and lives it excludes or pathologizes, and what its instability reveals about the social order it props up: [paste text, description, or context here]",
  },
  {
    kicker: "Unsettle",
    title: "Situate the Knowledge",
    description: "Hand over a knowledge claim, methodology, or form of expertise and get back a standpoint analysis: from whose position it speaks, what it cannot see, whose experience it generalizes, and what perspectives it forecloses.",
    prompt: "Situate [knowledge claim / methodology], identifying from whose standpoint it speaks, what it can and cannot see from that position, whose experience it generalizes, and what partial perspectives it forecloses: [paste text, description, or context here]",
  },
  {
    kicker: "Trace",
    title: "Trace the Network",
    description: "Hand over an assemblage, system, or controversy and get back an ANT-informed analysis: the human and nonhuman actors holding it together, the translations between them, and what breaks when you pull one out.",
    prompt: "Trace the network of [assemblage / system / controversy], following the human and nonhuman actors that hold it together, identifying the translations and enrollments that sustain it, and showing what breaks or becomes visible when a connection is severed: [paste text, description, or context here]",
  },
  {
    kicker: "Read",
    title: "Diagnose the Culture",
    description: "Hand over a cultural form, media text, or moment of common sense and get back a cultural-studies analysis: encoding and decoding, hegemonic meaning-making, and where contested readings emerge.",
    prompt: "Diagnose [cultural form / media text], identifying how meaning is encoded and decoded, what hegemonic common sense it reinforces or contests, whose cultural authority it draws on, and where oppositional readings emerge: [paste text, description, or context here]",
  },
  {
    kicker: "Trace",
    title: "Unsettle the Archive",
    description: "Hand over an archive, canon, or historical narrative and get back a postcolonial reading: what it preserves and silences, whose history it authorizes, and what epistemic violence it performs.",
    prompt: "Unsettle [archive / canon / narrative], identifying what it preserves and what it silences, whose history it authorizes and whose it erases, what epistemic violence it performs, and what counternarratives its gaps make possible: [paste text, description, or context here]",
  },
  {
    kicker: "Unsettle",
    title: "Estrange the Familiar",
    description: "Hand over a practice, ritual, or institution taken for granted and get back an ethnographic defamiliarization: the implicit rules, the social work being done, and what becomes visible when you treat the obvious as strange.",
    prompt: "Estrange [practice / institution], making the taken-for-granted strange, surfacing the implicit rules and social logics that organize it, identifying what ritual or symbolic work it performs, and showing what becomes visible when the obvious is treated as anthropologically remarkable: [paste text, description, or context here]",
  },
  {
    kicker: "Expose",
    title: "Follow the Money",
    description: "Hand over an industry, policy, or institution and get back a critical fiscal analysis: where capital flows, who is subsidized, what is financialized, and whose risks are socialized.",
    prompt: "Follow the money through [industry / policy], tracing where capital flows and where it accumulates, identifying who is subsidized and who bears the costs, what is financialized and what is devalued, and whose risks are socialized while whose profits are privatized: [paste text, description, or context here]",
  },
  {
    kicker: "Trace",
    title: "Scale the Analysis",
    description: "Hand over a development pattern, trade relation, or spatial arrangement and get back a world-systems analysis: core-periphery dynamics, uneven development, and what spatial fixes are at work.",
    prompt: "Scale the analysis of [development pattern / trade relation], identifying core-periphery dynamics, tracing patterns of uneven development, locating the spatial fixes and geographic displacements that sustain accumulation, and showing what the global structure reveals that the local frame conceals: [paste text, description, or context here]",
  },
  {
    kicker: "Read",
    title: "Theorize the Affect",
    description: "Hand over a cultural moment, text, or political formation and get back an affect-theoretical reading: what circulates beneath argument, what sticks, and what structures of feeling organize the moment.",
    prompt: "Theorize the affect operating through [cultural moment / text], identifying what circulates beneath explicit argument, what emotional intensities stick and to whom, what structures of feeling organize the moment, and how affective economies distribute attachment and aversion: [paste text, description, or context here]",
  },
  {
    kicker: "Unsettle",
    title: "Reframe the Disability",
    description: "Hand over a practice, policy, or design and get back a disability-studies analysis: medical vs social model, what counts as normal, whose bodies are accommodated, and what a crip perspective reveals.",
    prompt: "Reframe [practice / policy] through disability studies, identifying whether it operates through a medical or social model, what it treats as normal and what as deviant, whose bodies and minds are accommodated and whose are excluded, and what a crip perspective reveals about its assumptions: [paste text, description, or context here]",
  },
  {
    kicker: "Read",
    title: "Read the Artifact",
    description: "Hand over a practice, policy, or design and get back a disability-studies analysis: medical vs social model, what counts as normal, whose bodies are accommodated, and what a crip perspective reveals.",
    prompt: "Read [technology / media form / artifact] as a text, attending to its forms, conventions, material affordances, and constraints, to uncover what it enables or forecloses, as well as what assumptions, subject positions, and power relations it encodes and circulates within wider discourses: [paste text, description, or context here]",
  },
  {
    kicker: "Analyze",
    title: "Materialize the Discourse",
    description: "Hand over a phenomenon or debate and get back a new-materialist reading: matter's agency, human-nonhuman entanglements, and what discourse-only accounts miss.",
    prompt: "Materialize [phenomenon / debate], attending to matter's agency and resistance, tracing human-nonhuman entanglements, identifying what discourse-only accounts miss, and showing what becomes thinkable when materiality is taken seriously as a co-constitutive force: [paste text, description, or context here]",
  },
  {
    kicker: "Trace",
    title: "Provincialize the Universal",
    description: "Hand over a claim to universality and get back a decolonial reading: whose particular masquerades as universal, what knowledge systems it displaces, and what thinking from the margins reveals.",
    prompt: "Provincialize [universal claim / framework], identifying whose particular experience masquerades as universal, what knowledge systems and lifeworlds it displaces, what colonial matrix of power it reproduces, and what thinking from the margins and the underside of modernity reveals: [paste text, description, or context here]",
  },
  {
    kicker: "Read",
    title: "Narrate the Crisis",
    description: "Hand over a crisis or emergency and get back a critical narrative analysis: how the crisis is constructed, who gets to name it, what it authorizes, and whose crises are rendered invisible.",
    prompt: "Narrate [crisis / emergency], identifying how it is discursively constructed, who gets to name and frame it, what responses it authorizes and forecloses, what prior conditions it naturalizes as background, and whose ongoing crises are rendered invisible by its urgency: [paste text, description, or context here]",
  },
  {
    kicker: "Analyze",
    title: "Ecologize the Question",
    description: "Hand over an environmental issue, resource conflict, or land use and get back a political-ecology analysis: nature-society entanglements, metabolic rifts, and who bears the ecological costs.",
    prompt: "Ecologize [environmental issue / resource conflict], tracing nature-society entanglements, identifying metabolic rifts and ecological contradictions, showing how environmental costs are distributed along lines of race, class, and geography, and locating what political arrangements the category of 'nature' conceals: [paste text, description, or context here]",
  },
  {
    kicker: "Expose",
    title: "Interrogate the Data",
    description: "Hand over a dataset, metric, or classification system and get back a critical data analysis: what is counted and what is not, what categories enforce, and whose world the data encodes.",
    prompt: "Interrogate [dataset / metric / classification], identifying what is counted and what is rendered invisible, what political work the categories perform, whose world the data encodes and whose it erases, and what the infrastructure of measurement assumes about the phenomena it claims to represent: [paste text, description, or context here]",
  },
  {
    kicker: "Expose",
    title: "Surface the Racial Logic",
    description: "Hand over a law, policy, institution, or discourse and get back a CRT analysis: how race is constructed and operationalized, what colorblind frameworks obscure, whose interests racial arrangements serve, and what counternarratives reveal.",
    prompt: "Surface the racial logic of [law / institution], identifying how race is constructed and operationalized within it, what colorblind or post-racial frameworks obscure, how intersecting structures of class, gender, and citizenship compound its effects, and what counternarratives and lived experience reveal about its operations: [paste text, description, or context here]",
  },
  {
    kicker: "Read",
    title: "Frame the Interpretation",
    description: "Turn a text, a passage, or even a fragment into a serious interpretive claim with structure, stakes, and close reading.",
    prompt: "Produce a rigorous literary analysis of [text / passage / author], identifying the central interpretive problem, situating it among plausible readings, and drafting a structured argument with close reading, counterreadings, and a defensible thesis: [paste text or notes here]",
  },
  {
    kicker: "Reason",
    title: "Draft the Legal Brief",
    description: "Hand over the facts, issue, and forum, then ask for a full first-pass research memo with authorities and competing arguments.",
    prompt: "Prepare a first-pass legal research memorandum for a case about [issue, in jurisdiction], identifying the controlling authorities, governing standards, strongest arguments for each side, likely procedural posture, and a reasoned recommendation: [paste facts, authorities, or notes here]",
  },
  {
    kicker: "Administrative",
    title: "Survey the Field",
    description: "Make it sound entirely reasonable to outsource the whole review article: sources, patterns, debates, and provisional conclusions.",
    prompt: "Conduct a systematic literature review, bibliometric mapping, and synthetic meta-analysis of the research on [topic], identifying the major schools of thought, influential works, recurring methods, contested findings, and the clearest gaps for future inquiry: [paste sources, abstracts, or notes here]",
  },
  {
    kicker: "Fiction",
    title: "Craft the Screenplay",
    description: "Turn a premise into a scene or short script, with character established through action and dialogue and tension built according to the conventions of the form.",
    prompt: "Write a screenplay script, establishing character through action and dialogue, building scene-level tension, observing the visual grammar of the form, and calibrating pace and structure to the demands of the medium: [paste premise, characters, setting, genre, format, platform, or an existing scene here]",
  },
  {
    kicker: "Fiction",
    title: "Imagine the Speculative",
    description: "Develop a speculative premise — technological, social, or counterfactual — into a world with internal logic, character pressure, and a lived-in scene.",
    prompt: "Develop a speculative fiction premise about [concept / technology / social change / counterfactual], building out the internal logic of the world, the pressure it puts on its characters, and a scene or passage that makes it feel lived-in: [paste ideas, fragments, or constraints here]",
  },
  {
    kicker: "Administrative",
    title: "Draft the Grant Narrative",
    description: "Turn a project idea into a fundable narrative: significance, innovation, approach, and broader impact in the register of the funder.",
    prompt: "Draft a grant proposal and narrative for [project / funding body / field], articulating the significance of the problem, the innovation of the approach, the feasibility of the plan, and the broader impact of the work in the register and priorities of the funder: [paste project description, aims, or existing draft here]",
  },
  {
    kicker: "Administrative",
    title: "Write the Recommendation",
    description: "Give the facts and get back a draft that is warm, specific, and credible — calibrated to what selection committees in this context actually want to read.",
    prompt: "Write a letter of recommendation for [person / position / program], drawing on the specific evidence provided to make a case that is warm, credible, and particular rather than generic, calibrated to what selection committees in this context are actually looking for: [paste facts, anecdotes, the candidate's materials, or your notes here]",
  },
  {
    kicker: "Administrative",
    title: "Write the Abstract",
    description: "Turn a paper, project, or presentation into a tight abstract that does the work: stakes, method, argument, contribution — in the word count and register the venue requires.",
    prompt: "Write an abstract for this paper for [venue / audience], briefly summarizing the main thesis, then articulating the stakes, method, central argument, and contribution in the register the audience expects: [paste draft, notes, or key claims here]",
  },
  {
    kicker: "Administrative",
    title: "Review the Application",
    description: "Hand over an application packet and get back a structured review: what is strongest, what is weakest, how it fits the criteria, and a defensible recommendation.",
    prompt: "Review this application for [position / grant / program], identifying its strongest elements, its weakest, how well it fits the stated criteria, what questions or concerns it raises, and a defensible recommendation with reasoning: [paste application materials, rubric, or evaluation criteria here]",
  },
  {
    kicker: "Fiction",
    title: "Draft the Chapter",
    description: "Turn an outline, notes, or a rough draft into a chapter that moves — with scene, voice, pacing, and the pressure the reader needs to feel at this moment in the narrative.",
    prompt: "Draft a chapter for [novel / work in progress], establishing voice, managing scene and summary, building the pressure the reader needs to feel at this moment in the narrative, and ending with the right kind of tension or release: [paste outline, notes, existing draft, or context here]",
  },
  {
    kicker: "Educate",
    title: "Explain the Concept",
    description: "Hand over a concept, theory, or finding and get back a clear, accurate explanation calibrated to a specific audience — with the right analogies, the right level of abstraction, and no false simplifications.",
    prompt: "Write a one-page pop academic explainer on [concept / theory / finding] at a 10th grade reading level, with a concise introduction of background concepts, plus specific analogies that will resonate for a general audience: [paste the concept, your notes, or what the audience already knows here]",
  },
  {
    kicker: "Educate",
    title: "Design the Syllabus",
    description: "Hand over a course topic, level, and constraints and get back a structured syllabus: learning objectives, unit sequence, key readings, and an assessment structure that aligns with the stated goals.",
    prompt: "Design a syllabus for [topic / level / institution], specifying learning objectives, a weekly or unit sequence, key readings or resources, and an assessment structure that aligns with the stated goals: [paste constraints, existing materials, or institutional context here]",
  },
  {
    kicker: "Educate",
    title: "Write the Explainer",
    description: "Turn specialist knowledge into prose a smart non-specialist can follow — without dumbing it down or losing what actually matters.",
    prompt: "Write a clear, accurate explainer about [topic, for audience], translating specialist knowledge into prose a smart non-specialist can follow without losing what is genuinely at stake: [paste source material, technical notes, or key claims here]",
  },
];

export function getStarterPrompts() {
  const pool = STARTER_POOL.slice();
  for (let i = pool.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [pool[i], pool[j]] = [pool[j], pool[i]];
  }
  return [pool[0], ETHICS_PROMPT, pool[1], pool[2]];
}

export const PROMPT_ORDER = [
  "Expose",
  "Trace",
  "Read",
  "Analyze",
  "Unsettle",
  "Educate",
  "Fiction",
  "Administrative",
  "Reason",
];

export function getAllPrompts() {
  return [ETHICS_PROMPT, ...STARTER_POOL];
}
